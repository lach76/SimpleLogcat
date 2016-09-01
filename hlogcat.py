#!/usr/bin/env python

#   Code referenced colored_logcat.py (https://github.com/marshall/logcat-color)
#   Code referenced stackoverflow (http://stackoverflow.com/questions/11524586/accessing-logcat-from-android-via-python)

import os, sys, re, StringIO
import fcntl, termios, struct

import time
import Queue
import subprocess
import threading
import datetime

import tty
def getch():
    fd = sys.stdin.fileno()
    old_settings = termios.tcgetattr(fd)
    try:
        tty.setraw(fd)
        ch = sys.stdin.read(1)
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)

    return ch


class AsynchronousFileReader(threading.Thread):
    def __init__(self, fd, queue):
        assert isinstance(queue, Queue.Queue)
        assert callable(fd.readline)
        threading.Thread.__init__(self)
        self._fd = fd
        self._queue = queue

    def run(self):
        for line in iter(self._fd.readline, ''):
            self._queue.put(line)

    def eof(self):
        return not self.is_alive() and self._queue.empty()

def format(fg=None, bg=None, bright=False, bold=False, dim=False, reset=False):
    codes = []
    if reset: codes.append("0")
    else:
        if not fg is None: codes.append("3%d" % (fg))
        if not bg is None:
            if not bright: codes.append("4%d" % (bg))
            else: codes.append("10%d" % (bg))
        if bold: codes.append("1")
        elif dim: codes.append("2")
        else: codes.append("22")
    return "\033[%sm" % (";".join(codes))
    

def indent_wrap(message, indent=0, width=80):
    wrap_area = width - indent
    messagebuf = StringIO.StringIO()
    current = 0
    while current < len(message):
        next = min(current + wrap_area, len(message))
        messagebuf.write(message[current:next])
        if next < len(message):
            messagebuf.write("\n%s" % (" " * indent))
        current = next
    return messagebuf.getvalue()

def allocate_color(tag):
    # this will allocate a unique format for the given tag
    # since we dont have very many colors, we always keep track of the LRU
    if not tag in KNOWN_TAGS:
        KNOWN_TAGS[tag] = LAST_USED[0]
    color = KNOWN_TAGS[tag]
    LAST_USED.remove(color)
    LAST_USED.append(color)
    return color

def allocate_color_pid(pid):
    # this will allocate a unique format for the given tag
    # since we dont have very many colors, we always keep track of the LRU
    color = LAST_USED[int(pid) % 6]
    return color


def print_line(line):
    match = retag.match(line)
    if not match is None:
        date, time, pid, tid, tagtype, tag, message = match.groups()
        linebuf = StringIO.StringIO()

        # center process info
        #owner = owner.strip()
        tag = tag.strip()
        color = allocate_color_pid(pid)
		
        if len(pid) == 2: pid = " " + pid
        if len(pid) == 1: pid = "  " + pid
        if len(tid) == 2: tid += " "
        if len(tid) == 1: tid += "  "
		
        time = time + ' ' + pid + '/' + tid
        if color == BLACK:
            linebuf.write("%s%s%s " % (format(bg=WHITE, fg=color, dim=False), time, format(reset=True)))
	else:
	    linebuf.write("%s%s%s " % (format(fg=color, dim=False), time, format(reset=True)))
	
		
        # right-align tag title and allocate color if needed
         
        tag = tag[-TAG_WIDTH:].rjust(TAG_WIDTH)

        if color == BLACK:
	    linebuf.write("%s%s %s" % (format(bg=WHITE, fg=color, dim=False), tag, format(reset=True)))
        else:
	    linebuf.write("%s%s %s" % (format(fg=color, dim=False), tag, format(reset=True)))

        # write out tagtype colored edge
        if not tagtype in TAGTYPES: return
        linebuf.write(TAGTYPES[tagtype])

        # insert line wrapping as needed
        if tagtype == "E":
            if len(sys.argv) == 2:
                message = message.replace(sys.argv[1], highlight_err)
            linebuf.write("%s%s%s" % (format(fg=RED, bold=True), message, format(reset=True)))
        #elif tagtype == "W":
        #    linebuf.write("%s%s%s" % (format(fg=YELLOW, bold=True, dim=True), message, format(reset=True)))
        else:
            if len(sys.argv) == 2:
                message = message.replace(sys.argv[1], highlight)
            linebuf.write(message)

        #message = indent_wrap(message, HEADER_SIZE, WIDTH)

        line = linebuf.getvalue()

    print line

mStillRunning = True
mPauseLog = False

def print_title(data):
    print "%s%s%s" % (format(fg=YELLOW, bold=True), data, format(reset=True))

def print_text(data):
    print "%s%s%s" % (format(fg=WHITE, bold=False), data, format(reset=True))

def print_notice(data):
    print "%s%s%s" % (format(fg=GREEN, bold=False), data, format(reset=True))

def print_help():
    print_title("----------   Help  ----------")
    print_text("/          : ready to read command")
    print_text("Ctrl+C     : exit hlogcat")
    print_text("")
    print_title("---------- Command ----------")
    print_text("show       : show all filter information")
    print_text("help       : display current help")
    print_text("exit       : exit log sytem")
    print_text("mode       : filter mode : mode [mask|unmask]")
    print_text("module     : set filter for specific module : module XXXX:[VDIWEFS]")
    print_text("pid        : set filter for specific pid : pid XXXX:[VDIWEFS]")
    print_text("any        : set filter for any string in message : any XXXX")
    print_text("unmodule   : unset filter for module : unmodule XXXXX")
    print_text("unpid      : unset filter for pid : unpid XXXXX")
    print_text("unany      : unset filter for any : unany XXXX")
    print_text("")

FilterInfo = {"mode":True, "pid":{}, "module":{}, "any":{}}

def run_filter_command(command_list):
    global FilterInfo

    filter_mode = FilterInfo["mode"]
    filter_module = FilterInfo["module"]    
    filter_pid = FilterInfo["pid"]
    filter_any = FilterInfo["any"]

    if len(command_list) < 2:
        print_text("Error : argument is not matched - [%s]" % str(command_list))
        return

    command = command_list[0]
    arg = command_list[1]
    args = arg.split(':')

    arg1 = ''.join(args[0].split())
    
    if command == 'mode':
        if arg == 'mask':
            filter_mode = True
        else:
            filter_mode = False
    elif command == 'unmodule':
        if filter_module.has_key(arg1):
            del filter_module[arg1]
    elif command == 'unpid':
        if filter_pid.has_key(arg1):
            del filter_pid[arg1]
    elif command == 'unany':
        if filter_any.has_key(arg1):
            del filter_any[arg1]
    else:
        if len(args) == 2:
            if command == 'module':
                filter_module[arg1] = args[1] 
            elif command == 'pid':
                filter_pid[arg1] = args[1]
            elif command == 'any':
                filter_any[arg1] = args[1]
   
    print_title("Filter is set")
    print_filter_information()

def print_filter_information():
    global FilterInfo

    filter_mode = FilterInfo["mode"]
    filter_module = FilterInfo["module"]    
    filter_pid = FilterInfo["pid"]
    filter_any = FilterInfo["any"]

    if (filter_mode):
        print_title("Filter Mode : mask")
    else:
        print_title("Filter Mode : unmask")

    print_title("Module Filter List")
    for key, value in filter_module.items():
        print_text("-- [%s] : [%s]" % (key, value))

    print_title("PID Filter List")
    for key, value in filter_pid.items():
        print_text("-- [%s] : [%s]" % (key, value))

    print_title("Any Filter List")
    for key, value in filter_any.items():
        print_text("-- [%s] : [%s]" % (key, value))

def userInputThreadFunc():
    global mStillRunning
    global mPauseLog

    CTRL_C = chr(3)

    while (mStillRunning):
        ch = getch()

        mPauseLog = True
        print_notice("LOG is PAUSED")
        if (ch == chr(3)):      # Ctrl + C
            print "Exit HLogcat"
            mStillRunning = False
        elif ch == '?':
            print_help()
        elif (ch == '/') or (ch == chr(0x0d)):    # Command Input mode
            if ch == chr(0x0d):
                print_help()
            command = raw_input("/ ")
            command = command.lower()
            commands = command.split()
            if (len(commands) > 0):
                run_command = commands[0]
                if run_command == 'show':
                    print_filter_information()
                elif run_command == 'help':
                    print_help()
                elif run_command == 'exit':
                    print "Exit HLogCat"
                    mStillRunning = False
                else:
                    run_filter_command(commands)
        else:
            print_help()
        print_notice("LOG is RESUMED")
        mPauseLog = False

def isPrintable(pid, tag, tagtype, message):
    global FilterInfo

    filter_mode = FilterInfo["mode"]
    filter_module = FilterInfo["module"]    
    filter_pid = FilterInfo["pid"]
    filter_any = FilterInfo["any"]

    length = len(filter_module) + len(filter_pid) + len(filter_any)
    if length == 0:
        return True

    pid = ''.join(pid.split())
    tag = ''.join(tag.split())
    tagtype = ''.join(tag.split())
    message = ''.join(message.split())
    isFound = False;
    
    if filter_module.has_key(tag):
        isFound = True
    if filter_pid.has_key(pid):
        isFound = True
    for anykey, anyvalue in filter_any.items():
        message = message.decode('utf-8')
        if message.find(anykey) > 0:
            isFound = True
            break

    if filter_mode == False:
        isFound = not isFound
    return isFound

import json
def save_filter_info():
    global FilterInfo
    with open("hlogcat.json", "w") as file:
        json.dump(FilterInfo, file)

def load_filter_info():
    global FilterInfo
    if os.path.isfile("hlogcat.json"):
        with open("hlogcat.json", "r") as file:
            FilterInfo = json.load(file)

if __name__ == "__main__":
    # init terminal
    data = fcntl.ioctl(sys.stdout.fileno(), termios.TIOCGWINSZ, '1234')
    HEIGHT, WIDTH = struct.unpack('hh',data)
    BLACK, RED, GREEN, YELLOW, BLUE, MAGENTA, CYAN, WHITE = range(8)

    LAST_USED = [GREEN,YELLOW,BLUE,MAGENTA,CYAN,BLACK]
    KNOWN_TAGS = {
        "dalvikvm": BLUE,
        "Process": BLUE,
        "ActivityManager": CYAN,
        "ActivityThread": CYAN,
    }

    TAGTYPE_WIDTH = 3
    TAG_WIDTH = 25
    PROCESS_WIDTH = 21 #8 # 8 or -1
    HEADER_SIZE = TAGTYPE_WIDTH + 1 + TAG_WIDTH + 1 + PROCESS_WIDTH + 1

    TAGTYPES = {
        "V": "%s%s%s " % (format(fg=WHITE, bg=BLACK), "V".center(TAGTYPE_WIDTH), format(reset=True)),
        "D": "%s%s%s " % (format(fg=BLACK, bg=BLUE), "D".center(TAGTYPE_WIDTH), format(reset=True)),
        "I": "%s%s%s " % (format(fg=BLACK, bg=GREEN), "I".center(TAGTYPE_WIDTH), format(reset=True)),
        "W": "%s%s%s " % (format(fg=BLACK, bg=YELLOW), "W".center(TAGTYPE_WIDTH), format(reset=True)),
        "E": "%s%s%s " % (format(fg=BLACK, bg=RED), "E".center(TAGTYPE_WIDTH), format(reset=True)),
        "F": "%s%s%s " % (format(fg=BLACK, bg=RED), "F".center(TAGTYPE_WIDTH), format(reset=True)),
    }
    retag = re.compile("^([0-9][0-9]-[0-9][0-9]) ([0-9][0-9]:[0-9][0-9]:[0-9][0-9]\.[0-9][0-9][0-9])\s+(\d+)\s+(\d+) ([A-Z]) ([^:]*)[: +](.*)$")
    pid_out = '0'
    pid_btld = '0'

    load_filter_info()
    print_filter_information()


    # You'll need to add any command line arguments here.
    process = subprocess.Popen(['adb', 'logcat'], stdout=subprocess.PIPE)

    # Launch the asynchronous readers of the process' stdout.
    stdout_queue = Queue.Queue()
    stdout_reader = AsynchronousFileReader(process.stdout, stdout_queue)
    stdout_reader.start()

    mUserInputThread = threading.Thread(target=userInputThreadFunc)
    mUserInputThread.start()

    # Check the queues if we received some output (until there is nothing more to get).
    mStillRunning = True
    mPauseLog = False;
    try:
        while mStillRunning and not stdout_reader.eof():
            while not stdout_queue.empty() and not mPauseLog:
                printable = False

                line = stdout_queue.get()
                # TODO: Add filter.
                match = retag.match(line)
                if not match is None:
                    _date, _time, _pid, _tid, _tagtype, _tag, _message = match.groups()
                    if isPrintable(_pid.lower(), _tag.lower(), _tagtype.lower(), _message.lower()):
                        print_line(line)
                else:
                    print_line(line)
            time.sleep(0.1)

    except KeyboardInterrupt:
        mStillRunning = False;

    finally:
        save_filter_info()
        mStillRunning = False;
        mUserInputThread.join();
        process.kill()