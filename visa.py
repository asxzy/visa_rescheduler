import smtplib
import time
import json
import random
import configparser
from datetime import datetime

import requests
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait as Wait
from selenium.webdriver.common.by import By
from webdriver_manager.chrome import ChromeDriverManager

from sendgrid import SendGridAPIClient
from sendgrid.helpers.mail import Mail


config = configparser.ConfigParser()
config.read("config.ini")

USERNAME = config["INFO"]["USERNAME"]
PASSWORD = config["INFO"]["PASSWORD"]
SCHEDULE_ID = config["INFO"]["SCHEDULE_ID"]
MY_SCHEDULE_DATE = config["INFO"]["MY_SCHEDULE_DATE"]
COUNTRY_CODE = config["INFO"]["COUNTRY_CODE"]
FACILITY_ID = config["INFO"]["FACILITY_ID"]

SENDGRID_API_KEY = config["SENDGRID"]["SENDGRID_API_KEY"]
PUSH_TOKEN = config["PUSHOVER"]["PUSH_TOKEN"]
PUSH_USER = config["PUSHOVER"]["PUSH_USER"]
EMAIL_HOST = config["EMAIL"]["HOST"]
EMAIL_PORT = config["EMAIL"]["PORT"]
EMAIL_USERNAME = config["EMAIL"]["USERNAME"]
EMAIL_PASSWORD = config["EMAIL"]["PASSWORD"]

LOCAL_USE = config["CHROMEDRIVER"].getboolean("LOCAL_USE")
HUB_ADDRESS = config["CHROMEDRIVER"]["HUB_ADDRESS"]

REGEX_CONTINUE = f"//a[contains(text(),'{config['INFO']['CONTINUE']}')]"


def check_date_condition(month, day):
    return (int(month) == 10 and int(day) >= 15) and int(month) not in {9, 10}


STEP_TIME = 0.5  # time between steps (interactions with forms): 0.5 seconds
RETRY_TIME = 60 * 10  # wait time between retries/checks for available dates: 10 minutes
EXCEPTION_TIME = 60 * 30  # wait time when an exception occurs: 30 minutes
BANNED_COOLDOWN_TIME = 60 * 60  # wait time when temporary banned (empty list): 60 minutes

DATE_URL = f"https://ais.usvisa-info.com/{COUNTRY_CODE}/niv/schedule/{SCHEDULE_ID}/appointment/days/{FACILITY_ID}.json?appointments[expedite]=false"
TIME_URL = f"https://ais.usvisa-info.com/{COUNTRY_CODE}/niv/schedule/{SCHEDULE_ID}/appointment/times/{FACILITY_ID}.json?date=%s&appointments[expedite]=false"
APPOINTMENT_URL = f"https://ais.usvisa-info.com/{COUNTRY_CODE}/niv/schedule/{SCHEDULE_ID}/appointment"
EXIT = False


def send_notification(msg):
    print(f"Sending notification: {msg}")

    if SENDGRID_API_KEY:
        message = Mail(from_email=USERNAME, to_emails=USERNAME, subject=msg, html_content=msg)
        try:
            sg = SendGridAPIClient(SENDGRID_API_KEY)
            response = sg.send(message)
            print(response.status_code)
            print(response.body)
            print(response.headers)
        except Exception as e:
            print(e)

    if PUSH_TOKEN:
        url = "https://api.pushover.net/1/messages.json"
        data = {"token": PUSH_TOKEN, "user": PUSH_USER, "message": msg}
        requests.post(url, data)

    if EMAIL_HOST:
        sent_from = EMAIL_USERNAME
        to = set([EMAIL_USERNAME])
        subject = "US Visa Appointment Checker"

        email_text = ""
        email_text += f"From: {sent_from}"
        email_text += f"To: {', '.join(to)}"
        email_text += f"Subject: {subject}"
        email_text += f"Subject: {subject}"
        email_text += f"\n{msg}\n"

        try:
            server = smtplib.SMTP_SSL(EMAIL_HOST, EMAIL_PORT)
            server.ehlo()
            server.login(EMAIL_USERNAME, EMAIL_PASSWORD)
            server.sendmail(sent_from, to, email_text)
            server.close()

            print("Email sent!")
        except Exception as e:
            print("Something went wrong while sending the email")
            print(e)
            print(e.__traceback__)


def get_driver():
    if LOCAL_USE:
        dr = webdriver.Chrome(service=Service(ChromeDriverManager().install()))
    else:
        dr = webdriver.Remote(command_executor=HUB_ADDRESS, options=webdriver.ChromeOptions())
    return dr


driver = get_driver()


def login():
    # Bypass reCAPTCHA
    driver.get(f"https://ais.usvisa-info.com/{COUNTRY_CODE}/niv")
    time.sleep(STEP_TIME)
    a = driver.find_element(By.XPATH, '//a[@class="down-arrow bounce"]')
    a.click()
    time.sleep(STEP_TIME)

    print("Login start...")
    href = driver.find_element(By.XPATH, '//*[@id="header"]/nav/div[2]/div[1]/ul/li[3]/a')
    href.click()
    time.sleep(STEP_TIME)
    Wait(driver, 60).until(EC.presence_of_element_located((By.NAME, "commit")))

    print("\tclick bounce")
    a = driver.find_element(By.XPATH, '//a[@class="down-arrow bounce"]')
    a.click()
    time.sleep(STEP_TIME)

    do_login_action()


def do_login_action():
    print("\tinput email")
    user = driver.find_element(By.ID, "user_email")
    user.send_keys(USERNAME)
    time.sleep(random.randint(1, 3))

    print("\tinput pwd")
    pw = driver.find_element(By.ID, "user_password")
    pw.send_keys(PASSWORD)
    time.sleep(random.randint(1, 3))

    print("\tclick privacy")
    box = driver.find_element(By.CLASS_NAME, "icheckbox")
    box.click()
    time.sleep(random.randint(1, 3))

    print("\tcommit")
    btn = driver.find_element(By.NAME, "commit")
    btn.click()
    time.sleep(random.randint(1, 3))

    Wait(driver, 60).until(EC.presence_of_element_located((By.XPATH, REGEX_CONTINUE)))
    print("\tlogin successful!")


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
    print(f"Got time successfully! {date} {time}")
    return time


def reschedule(date):
    global EXIT
    print(f"Starting Reschedule ({date})")

    time = get_time(date)
    driver.get(APPOINTMENT_URL)

    data = {
        "utf8": driver.find_element(by=By.NAME, value="utf8").get_attribute("value"),
        "authenticity_token": driver.find_element(by=By.NAME, value="authenticity_token").get_attribute("value"),
        "confirmed_limit_message": driver.find_element(by=By.NAME, value="confirmed_limit_message").get_attribute("value"),
        "use_consulate_appointment_capacity": driver.find_element(
            by=By.NAME, value="use_consulate_appointment_capacity"
        ).get_attribute("value"),
        "appointments[asc_appointment][facility_id]": FACILITY_ID,
        "appointments[asc_appointment][date]": date,
        "appointments[asc_appointment][time]": time,
    }

    headers = {
        "User-Agent": driver.execute_script("return navigator.userAgent;"),
        "Referer": APPOINTMENT_URL,
        "Cookie": "_yatri_session=" + driver.get_cookie("_yatri_session")["value"],
    }

    r = requests.post(APPOINTMENT_URL, headers=headers, data=data)
    if r.text.find("Successfully Scheduled") != -1:
        global MY_SCHEDULE_DATE
        MY_SCHEDULE_DATE = date
        msg = f"Rescheduled Successfully! {date} {time}"
        send_notification(msg)
    else:
        msg = f"Reschedule Failed. {date} {time}"
        print(msg)
        send_notification(msg)


def is_logged_in():
    content = driver.page_source
    if content.find("error") != -1:
        return False
    return True


def print_dates(dates):
    print("Available dates:")
    for d in dates:
        print("%s \t business_day: %s" % (d.get("date"), d.get("business_day")))
    print()


last_seen = None


def get_available_date(dates):
    global last_seen

    def is_earlier(date):
        my_date = datetime.strptime(MY_SCHEDULE_DATE, "%Y-%m-%d")
        new_date = datetime.strptime(date, "%Y-%m-%d")
        result = my_date > new_date
        print(f"Is {my_date} > {new_date}:\t{result}")
        return result

    print("Checking for an earlier date:")
    for d in dates:
        date = d.get("date")
        if is_earlier(date) and date != last_seen:
            _, month, day = date.split("-")
            if check_date_condition(month, day):
                last_seen = date
                return date


if __name__ == "__main__":
    login()
    retry_count = 0
    while True:
        retry_count += 1
        try:
            print(f"attempt: {retry_count}")
            print(f"current schedule: {MY_SCHEDULE_DATE}")
            print("------------------")

            dates = get_date()
            if not dates:
                print(f"List is empty, possibility due to temporary ban. Sleep {BANNED_COOLDOWN_TIME}s before retrying")
                time.sleep(BANNED_COOLDOWN_TIME)
                continue

            print_dates(dates)
            date = get_available_date(dates)
            if date:
                print(f"New date: {date}")
                reschedule(date)
            else:
                print(f"No better date avaliable, currently scheduled for {MY_SCHEDULE_DATE}.")
                time.sleep(RETRY_TIME)

            if EXIT:
                print("------------------exit")
                break

        except KeyboardInterrupt:
            break
        except Exception as e:
            print(f"Failed to pull the dates from web. Retrying in {EXCEPTION_TIME}s")
            print(e)
            time.sleep(EXCEPTION_TIME)
