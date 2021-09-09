import requests_unixsocket
import time
#import base64
import subprocess
import multiprocessing
import queue
import threading
import signal
import os
import logging

# note: see https://aws.amazon.com/blogs/compute/parallel-processing-in-python-with-aws-lambda/



class HsdsApp:
    """
    Class to initiate and manage sub-process HSDS service
    """

    def __init__(self, username=None, password=None, logger=None, dn_count=1, logfile=None):
        """
        Initializer for class
        """
        # self._tempdir = tempfile.TemporaryDirectory()
        socket_dir = "%2Ftmp%2F"
        """
        for ch in self._tempdir.name:
            if ch == '/':
                socket_dir.append('%2F')
            else:
                socket_dir.append(ch)
        if self._tempdir.name[-1] != '/':
            socket_dir.append('%2F')
        socket_dir = "".join(socket_dir)
        """
         
        
        # socket_dir = "%2Ftmp%2F"  # TBD: use temp dir
        self._dn_urls = []
        self._processes = []
        self._queues = []
        self._threads = []
        self._dn_count = dn_count
        self._username = username
        self._password = password
        self._logfile = None

        if logger is None:
            self.log = logging
        else:
            self.log = logger

        
        for i in range(dn_count):
            dn_url = f"http+unix://{socket_dir}dn_{(i+1)}.sock"
            self._dn_urls.append(dn_url)

        # sort the ports so that node_number can be determined based on dn_url
        self._dn_urls.sort()
        self._endpoint = f"http+unix://{socket_dir}sn_1.sock"
        self._rangeget_url = f"http+unix://{socket_dir}rangeget.sock"


    @property
    def endpoint(self):
        return self._endpoint

    def print_process_output(self):
        """ print any queue output from sub-processes
        """
        #print("print_process_output")
        
        while True:
            got_output = False
            for q in self._queues:
                try:
                    line = q.get_nowait()  # or q.get(timeout=.1)
                except queue.Empty:
                    pass  # no output on this queue yet
                else:
                    if isinstance(line, bytes):
                        #self.log.debug(line.decode("utf-8").strip())
                        print(line.decode("utf-8").strip())
                    else:
                        print(line.strip())
                    got_output = True
            if not got_output:
                break  # all queues empty for now

    def check_processes(self):
        #print("check processes")
        self.print_process_output()
        for p in self._processes:
            if p.poll() is not None:
                result = p.communicate()
                msg = f"process {p.args[0]} ended, result: {result}"
                self.log.warn(msg)
                # TBD - restart failed process

    def run(self):
        """ startup hsds processes
        """
        if self._processes:
            # just check process state and restart if necessary
            self.check_processes()
            return

        dn_urls_arg = ""
        for dn_url in self._dn_urls:
            if dn_urls_arg:
                dn_urls_arg += ','
            dn_urls_arg += dn_url

        pout = subprocess.PIPE   # will pipe to parent
        # create processes for count dn nodes, sn node, and rangeget node
        count = self._dn_count + 2  # plus 2 for rangeget proxy and sn

        common_args = ["--standalone", ]
        # print("setting log_level to:", args.loglevel)
        # common_args.append(f"--log_level={args.loglevel}")
        common_args.append(f"--dn_urls={dn_urls_arg}") 
        common_args.append(f"--rangeget_url={self._rangeget_url}")
        common_args.append(f"--hsds_endpoint={self._endpoint}")
        common_args.append("--use_socket")

        for i in range(count):
            if i == 0:
                # args for service node
                pargs = ["hsds-servicenode", "--log_prefix=sn "]
                if self._username:
                    pargs.append(f"--hs_username={self._username}")
                if self._password:
                    pargs.append(f"--hs_password={self._password}")
                pargs.append(f"--sn_url={self._endpoint}")
                pargs.append("--logfile=sn1.log")
            elif i == 1:
                # args for rangeget node
                pargs = ["hsds-rangeget", "--log_prefix=rg "]
            else:
                node_number = i - 2  # start with 0
                pargs = ["hsds-datanode", f"--log_prefix=dn{node_number+1} "]
                pargs.append(f"--dn_urls={dn_urls_arg}")
                pargs.append(f"--node_number={node_number}")
            # logging.info(f"starting {pargs[0]}")
            pargs.extend(common_args)
            p = subprocess.Popen(pargs, bufsize=1, universal_newlines=True, shell=False, stdout=pout)
            self._processes.append(p)
            if not self._logfile:
                # setup queue so we can check on process output without blocking
                q = queue.Queue()
                t = threading.Thread(target=_enqueue_output, args=(p.stdout, q))
                self._queues.append(q)
                t.daemon = True  # thread dies with the program
                t.start()
                self._threads.append(t)

    def stop(self):
        """ terminate hsds processes
        """
        if not self._processes:
            return
        now = time.time()
        logging.info(f"hsds app stop at {now}")
        for p in self._processes:
            logging.info(f"sending SIGINT to {p.args[0]}")
            p.send_signal(signal.SIGINT)
        # wait for sub-proccesses to exit
        # wait for up to 2 seconds -- 20 * 0.1
        for i in range(20):
            is_alive = False
            for p in self._processes:
                if p.poll() is None:
                    is_alive = True
            if is_alive:
                logging.debug("still alive, sleep 0.1")
                time.sleep(0.1)

        # kill any reluctant to die processes        
        for p in self._processes:
            if p.poll():
                logging.info(f"terminating {p.args[0]}")
                p.terminate()
        self._processes = []
        for t in self._threads:
            del t
        self._threads = []

    def __del__(self):
        """ cleanup class resources """
        self.stop()
# 
# End HsdsApp class
#

def _enqueue_output(out, queue):
    for line in iter(out.readline, b''):
        queue.put(line)
    logging.debug("enqueue_output close()")
    out.close()

def make_request(method, req, hs_endpoint=None, params=None, headers=None, body=None):
    # invoke about request
    logging.debug(f"make_request: {hs_endpoint+req}")
    for k in params:
        v = params[k]
        logging.debug(f"param[{k}]: {v}")
    result = {}
    with requests_unixsocket.Session() as s:
        try:
            if method == "GET":
                rsp = s.get(hs_endpoint + req, params=params, headers=headers)
            elif method == "POST":
                rsp = s.post(hs_endpoint + req, params=params, headers=headers, data=body)
            elif method == "PUT":
                rsp = s.put(hs_endpoint + req, params=params, headers=headers, data=body)
            elif method == "DELETE":
                rsp = s.delete(hs_endpoint + req, params=params, headers=headers)
            else:
                msg = f"Unexpected request method: {method}"
                logging.error(msg)
                raise ValueError(msg)

            logging.info(f"got status_code: {rsp.status_code} from req: {req}")

            result["status_code"] = rsp.status_code

            #print_process_output(processes)
            if rsp.status_code == 200:
                logging.info(f"rsp.text: {rsp.text}")
                result["output"] = rsp.text
        except Exception as e:
            logging.error(f"got exception: {e}, quitting")
        except KeyboardInterrupt:
            logging.error("got KeyboardInterrupt, quitting")
        finally:
            logging.debug("request done")  
        return result    

def enqueue_output(out, queue):
    for line in iter(out.readline, b''):
        queue.put(line)
    logging.debug("enqueu_output close()")
    out.close()

def getEventMethod(event):
    method = "GET"  # default
    if "method" in event:
        method = event["method"]
    else:
        # scan for method in the api gateway 2.0 format
        if "requestContext" in event:
            reqContext = event["requestContext"]
            if "http" in reqContext:
                http = reqContext["http"]
                if "method" in http:
                    method = http["method"]
    return method

def getEventPath(event):
    path = "/about"  # default
    if "path" in event:
        path = event["path"]
    else:
         # scan for path in the api gateway 2.0 format
        if "requestContext" in event:
            reqContext = event["requestContext"]
            if "http" in reqContext:
                http = reqContext["http"]
                if "path" in http:
                    path = http["method"]
    return path

def getEventHeaders(event):
    headers = {}  # default
    if "headers" in event:
        headers = event["headers"]
    return headers

def getEventParams(event):
    params = {}  # default
    if "params" in event:
        params = event["params"]
    elif "queryStringParameters" in event:
        params = event["queryStringParameters"]
    return params

def getEventBody(event):
    body = {}  # default
    if "body" in event:
        body = event["body"]
    return body


def lambda_handler(event, context):
    # setup logging
    if "LOG_LEVEL" in os.environ:
        log_level_cfg = os.environ["LOG_LEVEL"]
    else:
        log_level_cfg = "INFO"
    if log_level_cfg == "DEBUG":
        log_level = logging.DEBUG
    elif log_level_cfg == "INFO":
        log_level = logging.INFO
    elif log_level_cfg in ("WARN", "WARNING"):
        log_level = logging.WARN
    elif log_level_cfg == "ERROR":
        log_level = logging.ERROR
    else:
        print(f"unsupported log_level: {log_level_cfg}, using INFO instead")
        log_level = logging.INFO
    print(f"setting LOG_LEVEL to {log_level_cfg}")

    logging.basicConfig(level=log_level)

    # process event data
    function_name = context.function_name
    logging.info(f"lambda_handler(event, context) for function {function_name}")
    if "AWS_ROLE_ARN" in os.environ:
        logging.debug(f"using AWS_ROLE_ARN: {os.environ['AWS_ROLE_ARN']}")
    if "AWS_SESSION_TOKEN" in os.environ:
        logging.debug(f"using AWS_SESSION_TOKEN: {os.environ['AWS_SESSION_TOKEN']}")
    logging.debug(f"event: {event}")
    method = getEventMethod(event)
    if method not in ("GET", "POST", "PUT", "DELETE"):
        err_msg = f"method: {method} is unsupported"
        logging.error(err_msg)
        return {"status_code": 400, "error": err_msg}

    headers = getEventHeaders(event)
    params = getEventParams(event)
    req = getEventPath(event)
 
    if not isinstance(headers, dict):
        err_msg = f"expected headers to be a dict, but got: {type(headers)}"
        logging.error(err_msg)
        return {"status_code": 400, "error": err_msg}
   
    if not isinstance(params, dict):
        err_msg = f"expected params to be a dict, but got: {type(params)}"
        logging.error(err_msg)
        return {"status_code": 400, "error": err_msg}
    
    body = getEventBody(event)
    if body and method not in ("PUT", "POST"):
        err_msg = "body only support with PUT and POST methods"
        logging.error(err_msg)
        return {"status_code": 400, "error": err_msg}

    cpu_count = multiprocessing.cpu_count()
    logging.info(f"got cpu_count of: {cpu_count}")
    if "TARGET_DN_COUNT" in os.environ:
        target_dn_count = int(os.environ["TARGET_DN_COUNT"])
    else:
        # base dn count on half the VCPUs (rounded up)
        target_dn_count = - (-cpu_count // 2)
    target_dn_count = 1 # test
    logging.info(f"setting dn count to: {target_dn_count}")

    # instantiate hsdsapp object
    hsds = HsdsApp(username=function_name, password="lambda", dn_count=target_dn_count)
    hsds.run()
    time.sleep(10)

    result = make_request(method, req, hs_endpoint=hsds.endpoint, params=params, headers=headers, body=body)
    logging.info(f"got result: {result}")
    hsds.stop()
    return result

### main
if __name__ == "__main__":
    # export PYTHONUNBUFFERED=1
    print("main")
    #req = "/about"
    req = "/datasets/d-d38053ea-3418fe27-22d9-478e7b-913279/value"
    #params = {}
    params = {"domain": "/shared/tall.h5", "bucket": "hdflab2"}

    
    class Context:
        @property
        def function_name(self):
            return "hslambda"
    
    # simplified event format
    # see: https://docs.aws.amazon.com/apigateway/latest/developerguide/http-api-develop-integrations-lambda.html
    # for a description of the API Gateway 2.0 format which is also supported
    event = {"method": "GET", "path": req, "params": params}
    context = Context()
    lambda_handler(event, context)

