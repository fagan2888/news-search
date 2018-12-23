__author__ = 'Niko'

HOST = 'snap.stanford.edu'  # Define which host in the .netrc file to use
CLIENT_DIR = '/home/newssearch/client'
#CLIENT_DIR = './'
SCRIPT_PID = '%s/pid' % CLIENT_DIR
NETRC = '%s/.netrc' % CLIENT_DIR
LOG_FILE = '%s/logClient.log' % CLIENT_DIR

WORKING_DIR = "%s/running_job" % CLIENT_DIR
HADOOP_OUT = '%s/hadoop-out.log' % WORKING_DIR
JOB_ID = '%s/jobID.txt' % WORKING_DIR
HADOOP_PID = '%s/pid.txt' % WORKING_DIR

QUEUE = 'http://snap.stanford.edu/news-search/api/queue.php'
COMMAND = '/usr/bin/hadoop jar %s/Spinn3rHadoop-0.0.2-SNAPSHOT.jar ' % CLIENT_DIR

import re
import os
import json
import time
import netrc
import shutil
import ntpath
import logging
import requests
import datetime
import subprocess

# read username and password
secrets = netrc.netrc(NETRC)
user, account, password = secrets.authenticators(HOST)

# init logging
# for production use INFO, to log status changes
# for debugging use DEBUG, to se a lot more
LOG_FORMAT ='%(asctime)s [%(levelname)s] %(message)s'
logging.basicConfig(filename=LOG_FILE, format=LOG_FORMAT, level=logging.INFO)
# set the logging lever for requests module to WARNING
# since at INFO it write a line for each requests!
# urllib3 needed for the server!
logging.getLogger('requests').setLevel(logging.WARNING)
logging.getLogger('urllib3').setLevel(logging.WARNING)
logging.debug('Client started')

# check if an instance of the script is running
# by checking for PID file
if os.path.isfile(SCRIPT_PID):
    ts = os.path.getmtime(SCRIPT_PID)
    pid_time = datetime.datetime.fromtimestamp(ts)
    diff_time = datetime.datetime.now() - pid_time
    diff_time = diff_time.seconds
    logging.error("STOPPING: Script PID already exists! "
                  "PID was created %s seconds ago." % str(diff_time))
    exit(-1)
else:
    # store PID
    script_pid = str(os.getpid())
    file(SCRIPT_PID, 'w').write(script_pid)


def load_file(link_, fname_):
    """
        Load one file dependency form the server.
    :param link_: the URL to the file on server
    :param fname_: the file name, used for the name of the local file
    :return:
    """
    r = requests.get(link_, auth=(user, password))
    if r.status_code == 200:
        f = open(fname_, 'w')
        f.write(r.text)
        f.close()
    else:
        logging.error("Got response with status code %s while loading file %s" % (r.status_code, fname_))


def read_command():
    """
        Read command from the file and join it into one string.
    :return:    arguments used for running the current job
    """
    arg = ''
    try:
        f = open('command', 'r')
        arg = ' '.join([i.strip() for i in f.readlines()])
        f.close()
    except Exception as e:
        logging.error("Error while reading command file! Message: " + e.message)
    return arg


def write_to_file(file_, content):
    """
        Write the provided content to a given file.
    :param file_:   the file to write to
    :param content: the content to be written
    :return:    None
    """
    try:
        f = open(file_, 'w')
        f.write(content)
        f.close()
    except Exception as e:
        logging.error("Error while writing content '%s' to file '%s'! Message: %s" % (content, file_, e.message))


def get_job_id():
    """
        Read the jobID from file.
    :return: jobID
    """
    if os.path.isfile(JOB_ID):
        return open(JOB_ID).readline().strip()
    else:
        logging.error("Job ID file is missing!")


def get_pid():
    """
        Read the pid from file.
    :return: pid
    """
    if os.path.isfile(HADOOP_PID):
        return int(open(HADOOP_PID).readline().strip())
    else:
        logging.error("PID file is missing!")


def get_haddop_out():
    """
        Read the pid from file.
    :return: pid
    """
    if os.path.isfile(HADOOP_OUT):
        return ''.join(open(HADOOP_OUT).readlines())
    else:
        logging.error("Hadoop out file is missing!")
        return ''


def check_pid(pid):
    """ Check For the existence of a unix pid. """
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    else:
        return True


def get_tracking_url(ofile):
    """
        Read the hadoop output file and get the tracking URL from it.
    :param ofile: the file where the hadoop output is written to
    :return:    the tracking URL for current running job
    """
    if os.path.isfile(ofile):
        for line in open(ofile).readlines():
            if 'The url to track the job' in line.strip():
                url = re.search("(?P<url>https?://[^\s]+)", line).group("url")
                return url
        logging.debug("The tracking URL is not present yet.")
    else:
        logging.error("Hadoop output file is missing!")
    return None


def report_progress(ofile):
    """
        Report the % of job completion.
    :param ofile: the file where the hadoop output is written to
    :return: The current job completion %.
    """
    progress = None
    if os.path.isfile(ofile):
        for line in open(ofile).readlines():
            if 'INFO mapreduce.Job:  map' in line.strip():
                line = line.split(' ')
                progress = line[6]+' '+line[8]
        if progress is None:
            logging.debug("The progress is not present yet.")
        return progress
    else:
        logging.error("Hadoop output file is missing!")
    return None


def get_exit_status(ofile):
    """
        Find whether the job has succeeded or failed.
    :param ofile: the hadoop output file
    :return:    True/False/None for success/fail/can-not-get-the-status
    """
    succ = None
    if os.path.isfile(ofile):
        for line in open(ofile).readlines():
            if 'completed successfully' in line.strip():
                succ = True
            elif 'failed with state' in line.strip():
                succ = False
            elif line.strip().startswith('ERROR: '):
                succ = False
    else:
        logging.error("Hadoop output file is missing!")
    return succ


def update_state(job_id, status=None, tracking=None, progress=None, hadoop_out=None):
    """
        Update the state of the currently running job back to the REST api.
    :param job_id:  id of the job
    :param status:  to which status we wish to update the job
    :param tracking:    the URL for tracking the job through hadoop
    :param progress:    the progress of the running job
    :return:    None
    """
    try:
        url_ = QUEUE + '/' + job_id

        # send URL
        data = {'action': 'update'}
        if status:
            data['status'] = status
        if tracking:
            data['tracking'] = tracking
        if progress:
            data['progress'] = progress
        if hadoop_out:
            data['hadoop_out'] = hadoop_out

        # encode to JSON
        dataJson = json.dumps(data)

        # set headers and send
        headers = {'Content-type': 'application/json',}
        r = requests.put(url_, data=dataJson, headers=headers, auth=(user, password))

        # check response code
        if r.status_code != 200:
            logging.error("Got response with status code %s while updating!" % r.status_code)
        logging.debug('Sent out data:' + dataJson)
    except Exception as e:
        logging.error("Error while updating status! Message: " + e.message)


#
# STEP 1 - CLEAN START
#
if not os.path.exists(WORKING_DIR):
    # load json
    r = requests.get(QUEUE, auth=(user, password))
    if r.status_code != 200:
        logging.error("Got response with status code %s while polling for new job!" % r.status_code)
    jsonData = r.json()

    # if there is a new job
    if jsonData['newJob']:
        # create folder
        os.mkdir(WORKING_DIR)
        os.chdir(WORKING_DIR)

        # get and store id
        jobID = jsonData['jobID']
        write_to_file(JOB_ID, jobID)

        logging.info("Starting job with remote ID %s." % jobID)

        # get dependencies
        for link in jsonData['dependencies'].values():
            f = ntpath.basename(link)
            load_file(link, f)

        # set status to submitted
        update_state(jobID, status='submitted')
        logging.info("Job %s set to submitted." % jobID)

        # read arguments, run the job and store PID
        arguments = read_command()
        p = subprocess.Popen(COMMAND + arguments + ' &> ' + HADOOP_OUT, shell=True)
        write_to_file(HADOOP_PID, str(p.pid))

        # set status to running
        logging.info("Job %s set to running." % jobID)
        update_state(jobID, status='running')
    else:
        logging.debug("No new job!")

#
# STEP 2 - UPDATING PROCESS AND CLEANING UP
#
elif os.path.isfile(HADOOP_PID):

    # move to working directory
    os.chdir(WORKING_DIR)

    # get jobID and pid
    jobID = get_job_id()
    script_pid = get_pid()

    # update status
    url = get_tracking_url(HADOOP_OUT)
    p = report_progress(HADOOP_OUT)
    update_state(jobID, progress=p, tracking=url)

    # if the job is done
    e = get_exit_status(HADOOP_OUT)
    if e is not None or not check_pid(get_pid()):
        if e:
            update_state(jobID, status='success')
            logging.info("Job %s succeeded." % jobID)
        else:
            update_state(jobID, status='fail')
            logging.info("Job %s failed." % jobID)

        # the process should be done by now
        # just to be sure, wait till it is not gone
        while check_pid(script_pid):
            time.sleep(1)

        # send hadoop out
        ho = get_haddop_out()
        update_state(jobID, hadoop_out=ho)

        # delete folder
        os.chdir('..')
        shutil.rmtree(WORKING_DIR)

# remove Script PID file
os.remove(SCRIPT_PID)