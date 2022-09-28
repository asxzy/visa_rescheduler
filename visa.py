import configparser
import json
import logging
import random
import re
import smtplib
import time
from datetime import datetime, timedelta

import requests
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait as Wait
from sendgrid import SendGridAPIClient
from sendgrid.helpers.mail import Mail
from webdriver_manager.chrome import ChromeDriverManager

logger = logging.getLogger()
logger.setLevel(logging.INFO)

handler = logging.StreamHandler()
formatter = logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")
handler.setFormatter(formatter)
logger.addHandler(handler)

config = configparser.ConfigParser()
config.read("config.ini")

USERNAME = config["SETUP"]["USERNAME"]
PASSWORD = config["SETUP"]["PASSWORD"]
SCHEDULE_ID = config["SETUP"]["SCHEDULE_ID"]
MY_SCHEDULE_DATE = config["SETUP"]["MY_SCHEDULE_DATE"]
COUNTRY_CODE = config["SETUP"]["COUNTRY_CODE"]
FACILITY_ID = config["SETUP"]["FACILITY_ID"]
RUN_FOREVER = config["SETUP"]["RUN_FOREVER"]


SENDGRID_API_KEY = config["SENDGRID"]["SENDGRID_API_KEY"]
PUSH_TOKEN = config["PUSHOVER"]["PUSH_TOKEN"]
PUSH_USER = config["PUSHOVER"]["PUSH_USER"]
EMAIL_HOST = config["EMAIL"]["HOST"]
EMAIL_PORT = config["EMAIL"]["PORT"]
EMAIL_USERNAME = config["EMAIL"]["USERNAME"]
EMAIL_PASSWORD = config["EMAIL"]["PASSWORD"]

LOCAL_USE = config["CHROMEDRIVER"].getboolean("LOCAL_USE")
HUB_ADDRESS = config["CHROMEDRIVER"]["HUB_ADDRESS"]

REGEX_CONTINUE = f"//a[contains(text(),'{config['SETUP']['CONTINUE']}')]"


def check_date_condition(date):
    # if len(MY_SCHEDULE_DATE) == 10:
    #     my_scheduled_dt = datetime.strptime(MY_SCHEDULE_DATE, "%Y-%m-%d")
    # else:
    #     my_scheduled_dt = datetime.strptime(MY_SCHEDULE_DATE, "%Y-%m-%d %H:%M")

    return date < MY_SCHEDULE_DATE
    # return (int(month) == 10 and int(day) >= 15) or int(month) not in {9, 10}


STEP_TIME = 0.5  # time between steps (interactions with forms): 0.5 seconds
RETRY_TIME = 60 * 2  # wait time between retries/checks for available dates: 10 minutes
EXCEPTION_TIME = 60 * 30  # wait time when an exception occurs: 30 minutes
BANNED_COOLDOWN_TIME = 60 * 60  # wait time when temporary banned (empty list): 60 minutes

LOGIN_URL = f"https://ais.usvisa-info.com/{COUNTRY_CODE}/niv"
INFO_URL = f"https://ais.usvisa-info.com/{COUNTRY_CODE}/niv/schedule/{SCHEDULE_ID}/appointment/print_instructions"
DATE_URL = (
    f"https://ais.usvisa-info.com/{COUNTRY_CODE}/niv/schedule/"
    f"{SCHEDULE_ID}/appointment/days/{FACILITY_ID}.json?appointments[expedite]=false"
)
TIME_URL = (
    f"https://ais.usvisa-info.com/{COUNTRY_CODE}/niv/schedule/"
    f"{SCHEDULE_ID}/appointment/times/{FACILITY_ID}.json?date=%s&appointments[expedite]=false"
)
APPOINTMENT_URL = f"https://ais.usvisa-info.com/{COUNTRY_CODE}/niv/schedule/{SCHEDULE_ID}/appointment"


# flag to check if exit needed
EXIT = False


def send_notification(msg):
    logging.info(f"Sending notification: {msg}")

    if SENDGRID_API_KEY:
        message = Mail(from_email=USERNAME, to_emails=USERNAME, subject=msg, html_content=msg)
        try:
            sg = SendGridAPIClient(SENDGRID_API_KEY)
            response = sg.send(message)
            logging.debug(response.status_code)
            logging.debug(response.body)
            logging.debug(response.headers)
        except Exception as e:
            logging.error(e)

    if PUSH_TOKEN:
        url = "https://api.pushover.net/1/messages.json"
        data = {"token": PUSH_TOKEN, "user": PUSH_USER, "message": msg}
        requests.post(url, data)

    if EMAIL_HOST:
        sent_from = EMAIL_USERNAME
        to = set([EMAIL_USERNAME])
        subject = "US Visa Appointment Checker"

        email_text = ""
        email_text += f"From: {sent_from}\n"
        email_text += f"To: {', '.join(to)}\n"
        email_text += f"Subject: {subject}\n\n"
        email_text += f"{msg}\n"

        try:
            server = smtplib.SMTP_SSL(EMAIL_HOST, EMAIL_PORT)
            server.ehlo()
            server.login(EMAIL_USERNAME, EMAIL_PASSWORD)
            server.sendmail(sent_from, to, email_text)
            server.close()

            logging.info("Email sent!")
        except Exception as e:
            logging.error("Something went wrong when sending the email")
            logging.error(e)
            logging.error(e.__traceback__)


def get_driver():
    if LOCAL_USE:
        dr = webdriver.Chrome(service=Service(ChromeDriverManager().install()))
    else:
        dr = webdriver.Remote(command_executor=HUB_ADDRESS, options=webdriver.ChromeOptions())
    return dr


driver = get_driver()


def login():
    # Bypass reCAPTCHA
    driver.get(LOGIN_URL)
    time.sleep(STEP_TIME)
    a = driver.find_element(By.XPATH, '//a[@class="down-arrow bounce"]')
    a.click()
    time.sleep(STEP_TIME)

    logging.info("Login start...")
    href = driver.find_element(By.XPATH, '//*[@id="header"]/nav/div[2]/div[1]/ul/li[3]/a')
    href.click()
    time.sleep(STEP_TIME)
    Wait(driver, 60).until(EC.presence_of_element_located((By.NAME, "commit")))

    logging.info("click bounce")
    a = driver.find_element(By.XPATH, '//a[@class="down-arrow bounce"]')
    a.click()
    time.sleep(STEP_TIME)

    do_login_action()


def do_login_action():
    logging.info("input email")
    user = driver.find_element(By.ID, "user_email")
    user.send_keys(USERNAME)
    time.sleep(random.randint(1, 3))

    logging.info("input pwd")
    pw = driver.find_element(By.ID, "user_password")
    pw.send_keys(PASSWORD)
    time.sleep(random.randint(1, 3))

    logging.info("click privacy")
    box = driver.find_element(By.CLASS_NAME, "icheckbox")
    box.click()
    time.sleep(random.randint(1, 3))

    logging.info("commit")
    btn = driver.find_element(By.NAME, "commit")
    btn.click()
    time.sleep(random.randint(1, 3))

    get_scheduled_date()

    Wait(driver, 60).until(EC.presence_of_element_located((By.XPATH, REGEX_CONTINUE)))
    logging.info("login successful!")


def get_scheduled_date():
    global MY_SCHEDULE_DATE

    # match pattern
    pattern = re.compile(
        r"(0?[1-9]|[12]\d|3[01])\s(January|February||March|April|May|June|July|August|September|October|November|December),\s20\d{2},\s([01]\d|2[0-3]):([0-5]\d)"
    )
    box = driver.find_element(By.CLASS_NAME, "consular-appt")
    # for element in content_box.find_elements_by_xpath(".//*"):
    print(box.text)
    match = pattern.search(box.text)
    print(match)
    if match:
        matched_text = match.group()
        date_month, year, time = matched_text.split(", ")
        day, month = date_month.split(" ")
        day = int(day)
        month = [
            "January",
            "February",
            "March",
            "April",
            "May",
            "June",
            "July",
            "August",
            "September",
            "October",
            "November",
            "December",
        ].index(month) + 1
        year = int(year)
        new_date = f"{year:4d}-{month:02d}-{day:02d} {time}"
        new_dt = datetime.strptime(new_date, r"%Y-%m-%d %H:%M")

        if datetime.now() < new_dt < datetime.now() + timedelta(weeks=100):
            MY_SCHEDULE_DATE = new_date
            logging.info(f"Current scheduled for {MY_SCHEDULE_DATE}")
        else:
            logging.warning("Failed to validate existing schduled date. Fallback to default date.")


def get_date():
    driver.get(DATE_URL)
    if not is_logged_in():
        login()
        return get_date()
    else:
        content = driver.find_element(By.TAG_NAME, "pre").text
        date = json.loads(content)
        return date


def get_time(date):
    time_url = TIME_URL % date
    driver.get(time_url)
    content = driver.find_element(By.TAG_NAME, "pre").text
    data = json.loads(content)
    time = data.get("available_times")[-1]
    logging.info(f"Got time successfully! {date} {time}")
    return time


def reschedule(date):
    logging.info(f"Starting Reschedule ({date})")

    time = get_time(date)
    driver.get(APPOINTMENT_URL)

    msg = f"Trying to reschedule for {date} {time}"
    logging.info(msg)
    send_notification(msg)

    data = {
        "utf8": driver.find_element(by=By.NAME, value="utf8").get_attribute("value"),
        "authenticity_token": driver.find_element(by=By.NAME, value="authenticity_token").get_attribute("value"),
        "confirmed_limit_message": driver.find_element(by=By.NAME, value="confirmed_limit_message").get_attribute("value"),
        "use_consulate_appointment_capacity": driver.find_element(
            by=By.NAME, value="use_consulate_appointment_capacity"
        ).get_attribute("value"),
        "appointments[consulate_appointment][facility_id]": FACILITY_ID,
        "appointments[consulate_appointment][date]": date,
        "appointments[consulate_appointment][time]": time,
        #     "appointments[asc_appointment][facility_id]": FACILITY_ID,
        #     "appointments[asc_appointment][date]": date,
        #     "appointments[asc_appointment][time]": time,
    }

    headers = {
        "User-Agent": driver.execute_script("return navigator.userAgent;"),
        "Referer": APPOINTMENT_URL,
        "Cookie": "_yatri_session=" + driver.get_cookie("_yatri_session")["value"],
    }

    r = requests.post(APPOINTMENT_URL, headers=headers, data=data)
    if r.text.lower().find("successfully") != -1:
        global MY_SCHEDULE_DATE
        MY_SCHEDULE_DATE = date
        msg = f"Rescheduled Successfully! {date} {time}"
        logging.info(msg)
        send_notification(msg)
    else:
        msg = f"Reschedule Failed. {date} {time}\nServer response:\n{r.text}"
        logging.info(msg)
        send_notification(msg)


def is_logged_in():
    content = driver.page_source
    if content.find("error") != -1:
        return False
    return True


def print_dates(dates):
    logging.info("Available dates:")
    for d in dates[:3]:
        logging.info(f"{d['date']} \t business_day: {d['business_day']}")


def get_available_date(dates):
    global MY_SCHEDULE_DATE

    # def is_earlier(my_date, date):
    #     new_date = datetime.strptime(date, "%Y-%m-%d")
    #     result = my_date > new_date
    #     return result

    logging.info("Checking for an earlier date:")

    for d in dates:
        date = d["date"]
        if check_date_condition(date):
            return date


if __name__ == "__main__":
    login()
    retry_count = 0
    while True:
        retry_count += 1
        try:
            logging.info(f"attempt: {retry_count}")
            logging.info(f"current schedule: {MY_SCHEDULE_DATE}")
            logging.info("------------------")

            dates = get_date()
            if not dates:
                logging.info(f"List is empty, possibility due to temporary ban. Sleep {BANNED_COOLDOWN_TIME}s before retrying")
                time.sleep(BANNED_COOLDOWN_TIME)
                continue

            print_dates(dates)
            date = get_available_date(dates)
            if date:
                logging.info(f"New date: {date}")
                reschedule(date)
                if not RUN_FOREVER:
                    EXIT = True
            else:
                logging.info(f"No better date avaliable, currently scheduled for {MY_SCHEDULE_DATE}. Recheck in {RETRY_TIME}s")
                time.sleep(RETRY_TIME)

            if EXIT:
                logging.info("------------------exit")
                break

        except KeyboardInterrupt:
            break
        except Exception as e:
            logging.error(f"Failed to pull the dates from web. Retrying in {EXCEPTION_TIME}s. Recheck in {EXCEPTION_TIME}s")
            logging.error(e)
            time.sleep(EXCEPTION_TIME)
