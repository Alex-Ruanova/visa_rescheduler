import os
import sys
import time
import logging
from datetime import datetime
from dotenv import load_dotenv
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

from selenium import webdriver
from selenium.webdriver.support.ui import WebDriverWait, Select
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.common.exceptions import (
    TimeoutException,
    WebDriverException,
    NoSuchElementException,
)

# Load configuration from .env
load_dotenv()
USER_EMAIL = os.getenv('USER_EMAIL')
USER_PASS = os.getenv('USER_PASS')
CEDULA = os.getenv('CEDULA')
SMTP_SERVER = os.getenv('SMTP_SERVER', 'smtp.gmail.com')
SMTP_PORT = int(os.getenv('SMTP_PORT', 587))
SMTP_USER = os.getenv('SMTP_USER')
SMTP_PASS = os.getenv('SMTP_PASS')
YEAR_PARAM = int(os.getenv('YEAR_PARAM', datetime.now().year))
RECIPIENT_EMAIL = os.getenv('RECIPIENT_EMAIL', SMTP_USER)
LOCALE = os.getenv('LOCALE', 'es-mx')

# Logging configuration
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger(__name__)


def setup_driver():
    options = webdriver.ChromeOptions()
    options.add_argument('--ignore-certificate-errors')
    options.add_argument('--ignore-ssl-errors')
    try:
        driver = webdriver.Chrome(options=options)
        driver.implicitly_wait(5)
        return driver
    except WebDriverException as e:
        logger.error('Error initializing WebDriver: %s', e)
        sys.exit(1)


def login(driver):
    login_url = f"https://ais.usvisa-info.com/{LOCALE}/niv/users/sign_in"
    driver.get(login_url)
    try:
        # Accept policy checkbox
        try:
            label = WebDriverWait(driver, 5).until(
                EC.element_to_be_clickable((By.CSS_SELECTOR, "label[for='policy_confirmed']"))
            )
            label.click()
            logger.info('Accepted policy')
        except TimeoutException:
            logger.debug('Policy label not found')
        driver.execute_script("document.getElementById('policy_confirmed').checked = true;")

        # Enter credentials
        email = WebDriverWait(driver, 10).until(
            EC.presence_of_element_located((By.ID, 'user_email'))
        )
        email.clear(); email.send_keys(USER_EMAIL)
        pwd = WebDriverWait(driver, 10).until(
            EC.presence_of_element_located((By.ID, 'user_password'))
        )
        pwd.clear(); pwd.send_keys(USER_PASS)

        # Submit login
        WebDriverWait(driver, 10).until(
            EC.element_to_be_clickable((By.CSS_SELECTOR, 'input[type="submit"]'))
        ).click()
        logger.info('Login submitted')
        time.sleep(5)
    except Exception as e:
        logger.exception('Login failed: %s', e)
        driver.quit(); sys.exit(1)


def navigate_to_appointment_page(driver):
    try:
        # Click group actions link
        group_link = WebDriverWait(driver, 15).until(
            EC.element_to_be_clickable((By.CSS_SELECTOR, f"a[href*='/{LOCALE}/niv/schedule/']"))
        )
        group_link.click()
        logger.info('Clicked group actions')

        # Open reprogram panel
        panel_link = WebDriverWait(driver, 15).until(
            EC.element_to_be_clickable((By.LINK_TEXT, 'Reprogramar cita'))
        )
        panel_link.click()
        logger.info('Opened reprogram panel')

        # Click the actual appointment calendar link
        btn = WebDriverWait(driver, 15).until(
            EC.element_to_be_clickable((By.XPATH, "//a[contains(@href, '/appointment') and contains(text(), 'Reprogramar cita')]") )
        )
        btn.click()
        logger.info('Navigated to appointment calendar')

        # Wait for appointment URL
        WebDriverWait(driver, 10).until(
            EC.url_contains(f"/{LOCALE}/niv/schedule/{CEDULA}/appointment")
        )
    except Exception as e:
        logger.exception('Failed to reach appointment calendar: %s', e)
        driver.quit(); sys.exit(1)


def find_first_available_date(driver):
    try:
        logger.info('Appointment URL: %s', driver.current_url)
        driver.save_screenshot('before_datepicker.png')

        # Open datepicker
        date_picker = WebDriverWait(driver, 20).until(
            EC.element_to_be_clickable((By.ID, 'appointments_consulate_appointment_date'))
        )
        date_picker.click()
        WebDriverWait(driver, 20).until(
            EC.presence_of_element_located((By.CLASS_NAME, 'ui-datepicker-calendar'))
        )

        # Iterate months
        while True:
            all_days = driver.find_elements(
                By.CSS_SELECTOR,
                ".ui-datepicker-calendar td[data-handler='selectDay']"
            )
            if all_days:
                # Take first available day element
                first_td = all_days[0]
                day_text = first_td.find_element(By.TAG_NAME, 'a').text
                # Extract month/year from the td attributes
                month_idx = int(first_td.get_attribute('data-month')) + 1
                year_val = int(first_td.get_attribute('data-year'))
                formatted = f"{day_text}-{month_idx}-{year_val}"
                logger.info('Found available date: %s', formatted)
                # Click the date link
                first_td.click()
                return formatted

            # Move to next month
            title = driver.find_element(By.CLASS_NAME, 'ui-datepicker-title').text
            month_name, year = title.split()
            if int(year) > YEAR_PARAM:
                logger.warning('Reached year %s without finding slots up to %s', year, YEAR_PARAM)
                driver.quit(); sys.exit(0)

            driver.find_element(By.CSS_SELECTOR, '.ui-datepicker-next').click()
            time.sleep(1)
    except Exception as e:
        logger.exception('Failed to select first available date: %s', e)
        driver.quit(); sys.exit(1)


def select_time_slot(driver, element_id):
    try:
        WebDriverWait(driver, 20).until(
            EC.presence_of_element_located((By.ID, element_id))
        )
        select = Select(driver.find_element(By.ID, element_id))
        if len(select.options) > 1:
            select.select_by_index(1)
            choice = select.first_selected_option.text
            logger.info('Selected time slot: %s', choice)
            return choice
        else:
            logger.warning('No time slots available for %s', element_id)
            return None
    except Exception as e:
        logger.exception('Error selecting time slot %s: %s', element_id, e)
        driver.quit(); sys.exit(1)


def send_email_notification(date_str):
    subject = 'Notificación - Agendamiento de Turno'
    body = f'Se ha encontrado una fecha disponible para la cita: {date_str}.'
    msg = MIMEMultipart()
    msg['From'] = SMTP_USER
    msg['To'] = RECIPIENT_EMAIL
    msg['Subject'] = subject
    msg.attach(MIMEText(body, 'plain'))

    try:
        server = smtplib.SMTP(SMTP_SERVER, SMTP_PORT)
        server.starttls()
        server.login(SMTP_USER, SMTP_PASS)
        server.sendmail(SMTP_USER, RECIPIENT_EMAIL, msg.as_string())
        server.quit()
        logger.info('Notification email sent to %s', RECIPIENT_EMAIL)
    except Exception as e:
        logger.error('Failed to send email: %s', e)


def main():
    driver = setup_driver()
    try:
        login(driver)
        navigate_to_appointment_page(driver)
        date = find_first_available_date(driver)
        select_time_slot(driver, 'appointments_consulate_appointment_time')
        send_email_notification(date)
    finally:
        logger.info('Closing browser')
        driver.quit()


if __name__ == '__main__':
    main()
