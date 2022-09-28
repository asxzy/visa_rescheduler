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


class USVisaRescheduler:

    step_time = 0.5  # time between steps (interactions with forms): 0.5 seconds
    retry_time = 60 * 2  # wait time between retries/checks for available dates: 10 minutes
    exception_time = 60 * 30  # wait time when an exception occurs: 30 minutes
    banned_cooldown_time = 60 * 60  # wait time when temporary banned (empty list): 60 minutes

    def __init__(self):
        self._set_logger()
        self._parse_config()
        self.build_url()
        # flag to check if exit needed
        self._exit = False
        self.get_driver()

    def _set_logger(self):
        handler = logging.StreamHandler()
        formatter = logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")
        handler.setFormatter(formatter)

        self.logger = logging.getLogger()
        self.logger.setLevel(logging.INFO)
        self.logger.addHandler(handler)

    def _parse_config(self):
        config = configparser.ConfigParser()
        config.read("config.ini")

        self._applicant_username = config["SETUP"]["USERNAME"]
        self._applicant_password = config["SETUP"]["PASSWORD"]
        self._applicant_schedule_id = config["SETUP"]["SCHEDULE_ID"]
        self._scheduled_date = config["SETUP"]["MY_SCHEDULE_DATE"]
        self._applicant_country_code = config["SETUP"]["COUNTRY_CODE"]
        self._applicant_facility_id = config["SETUP"]["FACILITY_ID"]
        self._run_forever = config["SETUP"]["RUN_FOREVER"]

        self._sendgrid_api_key = config["SENDGRID"]["SENDGRID_API_KEY"]
        self._pushover_token = config["PUSHOVER"]["PUSH_TOKEN"]
        self._pushover_user = config["PUSHOVER"]["PUSH_USER"]
        self._email_host = config["EMAIL"]["HOST"]
        self._email_port = config["EMAIL"]["PORT"]
        self._email_username = config["EMAIL"]["USERNAME"]
        self._email_password = config["EMAIL"]["PASSWORD"]

        self._local_use = config["CHROMEDRIVER"].getboolean("LOCAL_USE")
        self._local_uhub_address = config["CHROMEDRIVER"]["HUB_ADDRESS"]

        self._regex_continue = f"//a[contains(text(),'{config['SETUP']['CONTINUE']}')]"

    def check_date_condition(self, date):
        # if len(self._scheduled_date) == 10:
        #     my_scheduled_dt = datetime.strptime(self._scheduled_date, "%Y-%m-%d")
        # else:
        #     my_scheduled_dt = datetime.strptime(self._scheduled_date, "%Y-%m-%d %H:%M")

        return date < self._scheduled_date
        # return (int(month) == 10 and int(day) >= 15) or int(month) not in {9, 10}

    def build_url(self):
        self._login_url = f"https://ais.usvisa-info.com/{self._applicant_country_code}/niv"
        self._check_date_url = (
            f"https://ais.usvisa-info.com/{self._applicant_country_code}/niv/schedule/"
            f"{self._applicant_schedule_id}/appointment/days/{self._applicant_facility_id}.json?appointments[expedite]=false"
        )
        self._check_time_url = (
            f"https://ais.usvisa-info.com/{self._applicant_country_code}/niv/schedule/"
            f"{self._applicant_schedule_id}/appointment/times/{self._applicant_facility_id}.json?date=%s&appointments[expedite]=false"
        )
        self._appointment_url = (
            f"https://ais.usvisa-info.com/{self._applicant_country_code}/niv/schedule/{self._applicant_schedule_id}/appointment"
        )

    def send_notification(self, msg):
        self.logger.info(f"Sending notification: {msg}")

        if self._sendgrid_api_key:
            message = Mail(
                from_email=self._applicant_username, to_emails=self._applicant_username, subject=msg, html_content=msg
            )
            try:
                sg = SendGridAPIClient(self._sendgrid_api_key)
                response = sg.send(message)
                self.logger.debug(response.status_code)
                self.logger.debug(response.body)
                self.logger.debug(response.headers)
            except Exception as e:
                self.logger.error(e)

        if self._pushover_token:
            url = "https://api.pushover.net/1/messages.json"
            data = {"token": self._pushover_token, "user": self._pushover_user, "message": msg}
            requests.post(url, data)

        if self._email_host:
            sent_from = self._email_username
            to = set([self._email_username])
            subject = "US Visa Appointment Checker"

            email_text = ""
            email_text += f"From: {sent_from}\n"
            email_text += f"To: {', '.join(to)}\n"
            email_text += f"Subject: {subject}\n\n"
            email_text += f"{msg}\n"

            try:
                server = smtplib.SMTP_SSL(self._email_host, self._email_port)
                server.ehlo()
                server.login(self._email_username, self._email_password)
                server.sendmail(sent_from, to, email_text)
                server.close()

                self.logger.info("Email sent!")
            except Exception as e:
                self.logger.error("Something went wrong when sending the email")
                self.logger.error(e)
                self.logger.error(e.__traceback__)

    def get_driver(self):
        if self._local_use:
            self.driver = webdriver.Chrome(service=Service(ChromeDriverManager().install()))
        else:
            self.driver = webdriver.Remote(command_executor=self._local_uhub_address, options=webdriver.ChromeOptions())

    def login(self):
        # Bypass reCAPTCHA
        self.driver.get(self._login_url)
        time.sleep(self.step_time)
        a = self.driver.find_element(By.XPATH, '//a[@class="down-arrow bounce"]')
        a.click()
        time.sleep(self.step_time)

        self.logger.info("Login start...")
        href = self.driver.find_element(By.XPATH, '//*[@id="header"]/nav/div[2]/div[1]/ul/li[3]/a')
        href.click()
        time.sleep(self.step_time)
        Wait(self.driver, 60).until(EC.presence_of_element_located((By.NAME, "commit")))

        self.logger.info("click bounce")
        a = self.driver.find_element(By.XPATH, '//a[@class="down-arrow bounce"]')
        a.click()
        time.sleep(self.step_time)

        self.do_login_action()

    def do_login_action(self):
        self.logger.info("input email")
        user = self.driver.find_element(By.ID, "user_email")
        user.send_keys(self._applicant_username)
        time.sleep(random.randint(1, 3))

        self.logger.info("input pwd")
        pw = self.driver.find_element(By.ID, "user_password")
        pw.send_keys(self._applicant_password)
        time.sleep(random.randint(1, 3))

        self.logger.info("click privacy")
        box = self.driver.find_element(By.CLASS_NAME, "icheckbox")
        box.click()
        time.sleep(random.randint(1, 3))

        self.logger.info("commit")
        btn = self.driver.find_element(By.NAME, "commit")
        btn.click()
        time.sleep(random.randint(1, 3))

        self.get_scheduled_date()

        Wait(self.driver, 60).until(EC.presence_of_element_located((By.XPATH, self._regex_continue)))
        self.logger.info("login successful!")

    def get_scheduled_date(self):
        # match pattern
        pattern = re.compile(
            # match day
            r"(0?[1-9]|[12]\d|3[01])"
            # seperator
            r"\s"
            # month
            r"(January|February||March|April|May|June|July|August|September|October|November|December)"
            # seperator
            r",\s20\d{2},\s"
            # time
            r"([01]\d|2[0-3]):([0-5]\d)"
        )
        box = self.driver.find_element(By.CLASS_NAME, "consular-appt")
        # for element in content_box.find_elements_by_xpath(".//*"):
        match = pattern.search(box.text)
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
                self._scheduled_date = new_date
                self.logger.info(f"Retreive the scheduled date from web: {self._scheduled_date}")
            else:
                self.logger.warning("Failed to validate existing schduled date. Fallback to default date.")

    def get_date(self):
        self.driver.get(self._check_date_url)
        if not self.is_logged_in():
            self.login()
            return self.get_date()
        else:
            content = self.driver.find_element(By.TAG_NAME, "pre").text
            date = json.loads(content)
            return date

    def get_time(self, date):
        time_url = self._check_time_url % date
        self.driver.get(time_url)
        content = self.driver.find_element(By.TAG_NAME, "pre").text
        data = json.loads(content)
        time = data.get("available_times")[-1]
        self.logger.info(f"Got time successfully! {date} {time}")
        return time

    def reschedule(self, date):
        self.logger.info(f"Starting Reschedule ({date})")

        time = self.get_time(date)
        self.driver.get(self._appointment_url)

        msg = f"Trying to reschedule for {date} {time}"
        self.logger.info(msg)
        self.send_notification(msg)

        data = {
            "utf8": self.driver.find_element(by=By.NAME, value="utf8").get_attribute("value"),
            "authenticity_token": self.driver.find_element(by=By.NAME, value="authenticity_token").get_attribute("value"),
            "confirmed_limit_message": self.driver.find_element(by=By.NAME, value="confirmed_limit_message").get_attribute(
                "value"
            ),
            "use_consulate_appointment_capacity": self.driver.find_element(
                by=By.NAME, value="use_consulate_appointment_capacity"
            ).get_attribute("value"),
            "appointments[consulate_appointment][facility_id]": self._applicant_facility_id,
            "appointments[consulate_appointment][date]": date,
            "appointments[consulate_appointment][time]": time,
            # "appointments[asc_appointment][facility_id]": self._applicant_facility_id,
            # "appointments[asc_appointment][date]": date,
            # "appointments[asc_appointment][time]": time,
        }

        headers = {
            "User-Agent": self.driver.execute_script("return navigator.userAgent;"),
            "Referer": self._appointment_url,
            "Cookie": "_yatri_session=" + self.driver.get_cookie("_yatri_session")["value"],
        }

        r = requests.post(self._appointment_url, headers=headers, data=data)
        if r.text.lower().find("successfully") != -1:
            self._scheduled_date = date
            msg = f"Rescheduled Successfully! {date} {time}"
            self.logger.info(msg)
            self.send_notification(msg)
        else:
            msg = f"Reschedule Failed. {date} {time}\nServer response:\n{r.text}"
            self.logger.info(msg)
            self.send_notification(msg)

    def is_logged_in(self):
        content = self.driver.page_source
        if content.find("error") != -1:
            return False
        return True

    def print_dates(self, dates):
        self.logger.info("Available dates:")
        for d in dates[:3]:
            self.logger.info(f"{d['date']} \t business_day: {d['business_day']}")

    def get_available_date(self, dates):

        self.logger.info("Checking for an earlier date:")

        for d in dates:
            date = d["date"]
            if self.check_date_condition(date):
                return date

    def run(self):
        self.login()
        retry_count = 0
        while True:
            retry_count += 1
            try:
                self.logger.info(f"attempt: {retry_count}")
                self.logger.info(f"current schedule: {self._scheduled_date}")
                self.logger.info("------------------")

                dates = self.get_date()
                if not dates:
                    self.logger.info(
                        f"List is empty, possibility due to temporary ban. Sleep {self.banned_cooldown_time}s before retrying"
                    )
                    time.sleep(self.banned_cooldown_time)
                    continue

                self.print_dates(dates)
                date = self.get_available_date(dates)
                if date:
                    self.logger.info(f"New date: {date}")
                    self.reschedule(date)
                    if not self._run_forever:
                        self._exit = True
                else:
                    self.logger.info(
                        f"No better date avaliable, currently scheduled for {self._scheduled_date}. Recheck in {self.retry_time}s"
                    )
                    time.sleep(self.retry_time)

                if self._exit:
                    self.logger.info("------------------exit")
                    break

            except KeyboardInterrupt:
                break
            except Exception as e:
                self.logger.error(
                    f"Failed to pull the dates from web. Retrying in {self.exception_time}s. Recheck in {self.exception_time}s"
                )
                self.logger.error(e)
                time.sleep(self.exception_time)


if __name__ == "__main__":
    us_visa_rescheduler = USVisaRescheduler()
    us_visa_rescheduler.run()
