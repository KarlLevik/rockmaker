#
# Copyright 2019 Karl Levik
#

# Our imports:
import xlsxwriter
import pytds
import smtplib
from email import encoders
from email.mime.text import MIMEText
from email.mime.base import MIMEBase
from email.mime.multipart import MIMEMultipart
import logging
from logging.handlers import RotatingFileHandler
from datetime import datetime, timedelta, date
import sys, os

# Trick to make it work with both Python 2 and 3:
try:
  import configparser
except ImportError:
  import ConfigParser as configparser

# Configure logging
logger = logging.getLogger()
logger.setLevel(logging.DEBUG)
formatter = logging.Formatter('* %(asctime)s [id=%(thread)d] <%(levelname)s> %(message)s')
hdlr = RotatingFileHandler(filename='/tmp/plates_to_xlsx.log', maxBytes=1000000, backupCount=10)
hdlr.setFormatter(formatter)
logging.getLogger().addHandler(hdlr)

# Get input parameters, otherwise use default values
interval = 'month'

today = date.today()
first = today.replace(day=1)
prev_date = first - timedelta(days=1)

start_year = prev_date.year
start_month = prev_date.month
if len(sys.argv) > 1:
    interval = sys.argv[1]
    if interval not in ('month', 'year'):
        err_msg = 'interval must be "month" or "year"'
        logging.getLogger().error(err_msg)
        raise AttributeError(err_msg)
if len(sys.argv) > 2:
    start_year = sys.argv[2]  # e.g. 2018
if len(sys.argv) > 3:
    start_month = sys.argv[3] # e.g. 02
start_date = '%s/%s/01' % (start_year, start_month)

# Query to retrieve all plates registered and the number of times each has been imaged, within the reporting time frame:
sql = """SELECT pl.Barcode as "barcode",
    tn4.Name as "project",
    pl.DateDispensed as "date dispensed",
    count(it.DateImaged) as "imagings",
    u.Name as "user name",
    g.Name as "group name",
    c.Name as "plate type",
    st.Temperature as "setup temp",
    itemp.Temperature as "incub. temp"
FROM Plate pl
    INNER JOIN Experiment e ON pl.ExperimentID = e.ID
    INNER JOIN Containers c ON e.ContainerID = c.ID
    INNER JOIN Users u ON e.userID = u.ID
    INNER JOIN GroupUser gu ON u.ID = gu.UserID
    INNER JOIN Groups g ON g.ID = gu.GroupID
    INNER JOIN TreeNode tn1 ON pl.TreeNodeID = tn1.ID
    INNER JOIN TreeNode tn2 ON tn1.ParentID = tn2.ID
    INNER JOIN TreeNode tn3 ON tn2.ParentID = tn3.ID
    INNER JOIN TreeNode tn4 ON tn3.ParentID = tn4.ID
    INNER JOIN SetupTemp st ON e.SetupTempID = st.ID
    INNER JOIN IncubationTemp itemp ON e.IncubationTempID = itemp.ID
    LEFT OUTER JOIN ExperimentPlate ep ON ep.PlateID = pl.ID
    LEFT OUTER JOIN ImagingTask it ON it.ExperimentPlateID = ep.ID
WHERE pl.DateDispensed >= convert(date, '%s', 111) AND pl.DateDispensed < dateadd(%s, 1, convert(date, '%s', 111))
    AND ((it.DateImaged >= convert(date, '%s', 111) AND it.DateImaged < dateadd(%s, 1, convert(date, '%s', 111))) OR it.DateImaged is NULL)
	AND g.Name <> 'AllRockMakerUsers'
GROUP BY pl.Barcode,
    tn4.Name,
    pl.DateDispensed,
    u.Name,
    g.Name,
    c.Name,
    st.Temperature,
    itemp.Temperature
ORDER BY pl.DateDispensed ASC
""" % (start_date, interval, start_date, start_date, interval, start_date)

# Get the database credentials from the config file:
configuration_file = 'config.cfg'
config = configparser.RawConfigParser(allow_no_value=True)
if not config.read(configuration_file):
    msg = 'No configuration found at %s' % configuration_file
    logging.getLogger().exception(msg)
    raise AttributeError(msg)

credentials = None
if not config.has_section('RockMakerDB'):
    msg = 'No "RockMakerDB" section in configuration found at %s' % configuration_file
    logging.getLogger().exception(msg)
    raise AttributeError(msg)
else:
    credentials = dict(config.items('RockMakerDB'))

sender = None
recipients = None
if not config.has_section('Email'):
    msg = 'No "Email" section in configuration found at %s' % configuration_file
    logging.getLogger().exception(msg)
    raise AttributeError(msg)
else:
    email_settings = dict(config.items('Email'))
    sender = email_settings['sender']
    recipients = email_settings['recipients']

filename = None
# Connect to the database, create a cursor, actually execute the query, and write the results to an xlsx file:
with pytds.connect(**credentials) as conn:
    with conn.cursor() as c:
        c.execute(sql)

        filename = 'report_%s_%s-%s.xlsx' % (interval, start_year, start_month)
        filedir = '/tmp'
        filepath = os.path.join(filedir, filename)
        workbook = xlsxwriter.Workbook(filepath)
        worksheet = workbook.add_worksheet()
        
        bold = workbook.add_format({'bold': True})
        date_format = workbook.add_format({'num_format': 'yyyy-mm-dd hh:mm:ss'})

        worksheet.set_column('A:A', 8)
        worksheet.set_column('B:B', 30)
        worksheet.set_column('C:C', 20)
        worksheet.set_column('E:G', 20)
        worksheet.set_column('H:I', 11)

        worksheet.write('A1', 'barcode', bold)
        worksheet.write('B1', 'project', bold)
        worksheet.write('C1', 'date dispensed', bold)
        worksheet.write('D1', 'imagings', bold)
        worksheet.write('E1', 'user name', bold)
        worksheet.write('F1', 'group name', bold)
        worksheet.write('G1', 'plate type', bold)
        worksheet.write('H1', 'setup temp', bold)
        worksheet.write('I1', 'incub. temp', bold)

        i = 0
        for row in c.fetchall():
            i = i + 1
            j = 0
            for col in row:
                if j != 2:
                    worksheet.write(i, j, col)
                else:
                    worksheet.write(i, j, col, date_format)
                j = j + 1

        workbook.close()
        msg = 'Report available at %s' % filepath
        print(msg)
        logging.getLogger().debug(msg)

if filepath is not None and sender is not None and recipients is not None:
    message = MIMEMultipart()
    message['Subject'] = 'RockMaker plate report for %s starting %s' % (interval, start_date)
    message['From'] = sender
    message['To'] = recipients
    body = 'Please find the report attached.'
    message.attach(MIMEText(body, 'plain'))

    with open(filepath, 'rb') as attachment:
        part = MIMEBase('application', 'octet-stream')
        part.set_payload(attachment.read())

    encoders.encode_base64(part)

    part.add_header(
        'Content-Disposition',
        'attachment; filename= %s' % filename,
    )

    message.attach(part)
    text = message.as_string()

    try:
        server = smtplib.SMTP('localhost', 25) # or 587?
        #server.login('youremailusername', 'password')

        # Send the mail
        recipients_list = []
        for i in recipients.split(','):
            recipients_list.append(i.strip())
        server.sendmail(sender, recipients_list, text)
    except:
        err_msg = 'Failed to send email'
        logging.getLogger().exception(err_msg)
        print(err_msg)

    logging.getLogger().debug('Email sent')
