#!/usr/bin/env python
#
#   Code referenced colored_logcat.py (http://jsharkey.org/blog/2009/04/22/modifying-the-android-logcat-stream-for-full-color-debugging/)
#   Code referenced stackoverflow (http://stackoverflow.com/questions/11524586/accessing-logcat-from-android-via-python)

import os, sys, re, StringIO
import fcntl, termios, struct

import time
import Queue
import subprocess
import threading
import datetime

import tty
import json

BLACK, RED, GREEN, YELLOW, BLUE, MAGENTA, CYAN, WHITE = range(8)

def getch():
    fd = sys.stdin.fileno()
    old_settings = termios.tcgetattr(fd)
    try:
        tty.setraw(fd)
        ch = sys.stdin.read(1)
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)

    return ch

def format(fg=None, bg=None, bright=False, bold=False, dim=False, reset=False):
    codes = []
    if reset:
        codes.append("0")
    else:
        if not fg is None: codes.append("3%d" % (fg))
        if not bg is None:
            if not bright:
                codes.append("4%d" % (bg))
            else:
                codes.append("10%d" % (bg))
        if bold:
            codes.append("1")
        elif dim:
            codes.append("2")
        else:
            codes.append("22")
    return "\033[%sm" % (";".join(codes))


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

class LogcatPrint:
    mLastUsed = [GREEN, YELLOW, BLUE, MAGENTA, CYAN, BLACK]

    def __init__(self):
        self.mRegexTag = re.compile(
            "^([0-9][0-9]-[0-9][0-9]) ([0-9][0-9]:[0-9][0-9]:[0-9][0-9]\.[0-9][0-9][0-9])\s+(\d+)\s+(\d+) ([A-Z]) ([^:]*)[: +](.*)$")

        tagtypeWidth = 3
        self.mTagWidth = 25
        self.mTagTypes = {
            "V": "%s%s%s " % (format(fg=WHITE, bg=BLACK), "V".center(tagtypeWidth), format(reset=True)),
            "D": "%s%s%s " % (format(fg=BLACK, bg=BLUE), "D".center(tagtypeWidth), format(reset=True)),
            "I": "%s%s%s " % (format(fg=BLACK, bg=GREEN), "I".center(tagtypeWidth), format(reset=True)),
            "W": "%s%s%s " % (format(fg=BLACK, bg=YELLOW), "W".center(tagtypeWidth), format(reset=True)),
            "E": "%s%s%s " % (format(fg=BLACK, bg=RED), "E".center(tagtypeWidth), format(reset=True)),
            "F": "%s%s%s " % (format(fg=BLACK, bg=RED), "F".center(tagtypeWidth), format(reset=True)),
        }


    def indent_wrap(self, message, indent=0, width=80):
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

    def allocate_color_by_pid(self, pid):
        # this will allocate a unique format for the given tag
        # since we dont have very many colors, we always keep track of the LRU
        color = self.mLastUsed[int(pid) % 6]
        return color

    def regex_calc(self, line):
        return self.mRegexTag.match(line)

    def printlog(self, line):
        match = self.regex_calc(line)
        if not match is None:
            date, time, pid, tid, tagtype, tag, message = match.groups()
            linebuf = StringIO.StringIO()

            # center process info
            # owner = owner.strip()
            tag = tag.strip()
            color = self.allocate_color_by_pid(pid)

            if len(pid) == 2: pid = " " + pid
            if len(pid) == 1: pid = "  " + pid
            if len(tid) == 2: tid += " "
            if len(tid) == 1: tid += "  "

            time = time + ' ' + pid + '/' + tid
            time = time.ljust(27)
            if color == BLACK:
                linebuf.write("%s%s%s " % (format(bg=WHITE, fg=color, dim=False), time, format(reset=True)))
            else:
                linebuf.write("%s%s%s " % (format(fg=color, dim=False), time, format(reset=True)))

            # right-align tag title and allocate color if needed

            tag = tag[-self.mTagWidth:].rjust(self.mTagWidth)

            if color == BLACK:
                linebuf.write("%s%s %s" % (format(bg=WHITE, fg=color, dim=False), tag, format(reset=True)))
            else:
                linebuf.write("%s%s %s" % (format(fg=color, dim=False), tag, format(reset=True)))

            # write out tagtype colored edge
            if not tagtype in self.mTagTypes: return
            linebuf.write(self.mTagTypes[tagtype])

            # insert line wrapping as needed
            if tagtype == "E":
                linebuf.write("%s%s%s" % (format(fg=RED, bold=True), message, format(reset=True)))
            else:
                linebuf.write(message)

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

def print_err(data):
    print "%s%s%s" % (format(fg=RED, bold=False), data, format(reset=True))

gFilterInfo = {"mode": True, "pid": {}, "module": {}, "any": {}}


def cmd_showFilterProc(commandList = None):
    global gFilterInfo

    filter_process = gFilterInfo["process"]
    filter_mode = gFilterInfo["mode"]
    filter_module = gFilterInfo["module"]
    filter_pid = gFilterInfo["pid"]
    filter_any = gFilterInfo["any"]

    print_title("Filter Mode :")
    if (filter_mode):
        print_text("     display masked log")
    else:
        print_text("     display unmask log")

    print_title("Masked module list")
    for key, value in filter_module.items():
        print_text("     %s : %s" % (key, value))

    print_title("Masked Process List")
    for key, value in filter_process.items():
        print_text("    %s : %s" % (key, value))

    print_title("Masked PID list")
    for key, value in filter_pid.items():
        print_text("     %s : %s" % (key, value))

    print_title("Masked String in Message List")
    for key, value in filter_any.items():
        print_text("     %s : %s" % (key, value))

    return

gLogLevelString = 'VDIWEFS'

def cmd_showHelpProc(commandList = None):
    global gLogLevelString

    print_title("----------   Help  ----------")
    print_text("/        : ready to read command")
    print_text("Ctrl+C   : exit hlogcat")
    print_text("q / Q    : exit hlogcat")
    print_text("")
    print_title("---------- Command ----------")
    print_text("show       : show all filter information")
    print_text("help       : display current help")
    print_text("exit       : exit log sytem")
    print_text("mask       : display with masked filter")
    print_text("unmask     : display with unmasked filter")
    print_text("process    : set filter for specific process : process processname [%s]" % gLogLevelString)
    print_text("module     : set filter for specific module : module XXXX [%s]" % gLogLevelString)
    print_text("pid        : set filter for specific pid : pid XXXX [%s]" % gLogLevelString)
    print_text("any        : set filter for any string in message : any XXXX")
    print_text("uprocess   : unset filter for process : uprocess XXXXX")
    print_text("umodule   : unset filter for module : umodule XXXXX")
    print_text("upid      : unset filter for pid : upid XXXXX")
    print_text("uany      : unset filter for any : uany XXXX")
    print_text("")

    return

def cmd_exitProc(commandList):
    global mStillRunning

    mStillRunning = False

    return

def cmd_maskFilterProc(commandList):
    global gFilterInfo

    gFilterInfo['mode'] = True

    cmd_showFilterProc()
    return


def cmd_unmaskFilterProc(commandList):
    global gFilterInfo

    gFilterInfo['mode'] = False

    cmd_showFilterProc()

    return

def cmd_util_getLogLevel(loglevel):
    global gFilterInfo

    index = gLogLevelString.find(loglevel)
    if index >= 0:
        return gLogLevelString[index:]

    return None

def cmd_enableProcessFilterProc(commandList):
    global gFilterInfo;

    def err_print():
        print_err("     - usage : process processname [VDIWEFS]")

    processFilter = gFilterInfo["process"]
    if len(commandList) >= 2:
        processname = commandList[1]
        if len(commandList) == 3:
            loglevel = commandList[2].upper()
        else:
            loglevel = 'D'

        loglevel = cmd_util_getLogLevel(loglevel)
        if loglevel is not None:
            processFilter[processname] = loglevel
        else:
            err_print()

    cmd_showFilterProc()
    reloadProcessList()

    return

def cmd_enablePidFilterProc(commandList):
    global gFilterInfo

    def err_print():
        print_err("     - usage : pid pidno [VDIWEFS]")

    pidFilter = gFilterInfo['pid']
    if len(commandList) >= 2:
        pid = commandList[1]
        if len(commandList) == 3:
            loglevel = commandList[2].upper()
        else:
            loglevel = 'D'

        loglevel = cmd_util_getLogLevel(loglevel)
        if loglevel is not None:
            pidFilter[pid] = loglevel
        else:
            err_print()

    cmd_showFilterProc()

    return

def cmd_enableModuleFilterProc(commandList):
    global gFilterInfo

    def err_print():
        print_err("     - usage : module module [VDIWEFS]")

    moduleFilter = gFilterInfo['module']
    if len(commandList) >= 2:
        module = commandList[1]
        if len(commandList) == 3:
            loglevel = commandList[2].upper()
        else:
            loglevel = 'D'

        loglevel = cmd_util_getLogLevel(loglevel)
        if loglevel is not None:
            moduleFilter[module] = loglevel
        else:
            err_print()

    cmd_showFilterProc()

    return

def cmd_enableAnyMessageFilterProc(commandList):
    global gFilterInfo

    moduleAny = gFilterInfo['any']
    if len(commandList) == 2:
        module = commandList[1]
        moduleAny[module] = 'True'

    cmd_showFilterProc()

    return

def cmd_disableFilterProc(commandList):
    global gFilterInfo

    if len(commandList) == 2:
        command = commandList[0]
        arg = commandList[1]
        dict_key = command[1:]
        if gFilterInfo.has_key(dict_key):
            filterinfo = gFilterInfo[dict_key]
            if filterinfo.has_key(arg):
                del filterinfo[arg]
                print_text(" -- remove [%s] in [%s] is success" % (arg, command))

    return


gCommandList = {
    "show"  :   cmd_showFilterProc,
    "help"  :   cmd_showHelpProc,
    "exit"  :   cmd_exitProc,
    "mask"  :   cmd_maskFilterProc,
    "unmask":   cmd_unmaskFilterProc,
    "process":  cmd_enableProcessFilterProc,
    "pid"   :   cmd_enablePidFilterProc,
    "module":   cmd_enableModuleFilterProc,
    "any"   :   cmd_enableAnyMessageFilterProc,
    "uprocess": cmd_disableFilterProc,
    "upid"   :  cmd_disableFilterProc,
    "umodule": cmd_disableFilterProc,
    "uany": cmd_disableFilterProc
}

def cmd_runCommandProc(commandList):
    if len(commandList) > 0:
        command = commandList[0]
        if gCommandList.has_key(command):
            gCommandList[command](commandList)
            return True

    return False

def userInputThreadFunc():
    global mStillRunning
    global mPauseLog

    commandMode = False

    while (mStillRunning):
        ch = getch()

        mPauseLog = True
        if (ch == chr(3) or ch == 'q' or ch == 'Q'):  # Ctrl + C
            mStillRunning = False
        else:
            if commandMode == False:
                print_notice("------------ PAUSED ------------")
                print_notice("reload process list from shell")
                reloadProcessList()

                if ch != '/':
                    cmd_showHelpProc()
                commandMode = True

            mPauseLog = True
            while (commandMode):
                command = raw_input("/ ")
                command = command.lower()
                commands = command.split()

                process = cmd_runCommandProc(commands)
                if mStillRunning and process:
                    commandMode = True
                else:
                    commandMode = False
            mPauseLog = False
            print_notice("------------ RESUMED ------------")

gProcessList = {};
def reloadProcessList():
    global gProcessList

    output = subprocess.check_output(['adb', 'shell', 'ps'])
    lines = output.splitlines()
    for line in lines[1:]:
        items = line.split()
        pid = items[1]
        name = items[-1]
        gProcessList[str(pid)] = name

def isPrintable(pid, tag, tagtype, message):
    global gFilterInfo
    global gProcessList

    filter_mode = gFilterInfo["mode"]
    filter_module = gFilterInfo["module"]
    filter_pid = gFilterInfo["pid"]
    filter_any = gFilterInfo["any"]
    filter_process = gFilterInfo["process"]

    length = len(filter_module) + len(filter_pid) + len(filter_any)
    if length == 0:
        return True

    pid = ''.join(pid.split())
    tag = ''.join(tag.split())
    tagtype = ''.join(tagtype.split())
    message = ''.join(message.split())

    isFoundTag = False
    isFoundTagType = False

    tagtype = tagtype.upper()
    tag = tag.decode('utf-8')
    message = message.decode('utf-8')
    # change it to grep (substring mode)
    for key, value in filter_module.items():
        if (tag.find(key) >= 0):
            if (value.find(tagtype) >= 0):
                isFoundTagType = True

            isFoundTag = True
            break
    #if filter_module.has_key(tag):
    #    isFound = True
    if filter_pid.has_key(pid):
        isFoundTag = True
        if filter_pid[pid].find(tagtype) >= 0:
            isFoundTagType = True

    for anykey, anyvalue in filter_any.items():
        if message.find(anykey) >= 0:
            isFoundTag = True
            isFoundTagType = True
            break

    if gProcessList.has_key(pid):
        processname = gProcessList[pid]
        for key, value in filter_process.items():
            if processname.find(key) >= 0:
                isFoundTag = True
                if value.find(tagtype) >= 0:
                    isFoundTagType = True

    if filter_mode:
        isFound = isFoundTag and isFoundTagType
    else:
        isFound = not isFoundTag

    return isFound


def save_filter_info():
    global gFilterInfo
    with open("hlogcat.json", "w") as file:
        json.dump(gFilterInfo, file)


def load_filter_info():
    global gFilterInfo
    if os.path.isfile("hlogcat.json"):
        with open("hlogcat.json", "r") as file:
            gFilterInfo = json.load(file)
        if not gFilterInfo.has_key('process'):
            gFilterInfo['process'] = {}

    reloadProcessList()

if __name__ == "__main__":
    # init terminal
    load_filter_info()
    cmd_showFilterProc()

    logPrint = LogcatPrint()
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
    mPauseLog = False
    try:
        while mStillRunning and not stdout_reader.eof():
            while not stdout_queue.empty() and not mPauseLog:
                printable = False

                line = stdout_queue.get()
                match = logPrint.regex_calc(line)
                if not match is None:
                    _date, _time, _pid, _tid, _tagtype, _tag, _message = match.groups()
                    if isPrintable(_pid.lower(), _tag.lower(), _tagtype.lower(), _message.lower()):
                        logPrint.printlog(line)
                else:
                    logPrint.printlog(line)
            time.sleep(0.1)

    except KeyboardInterrupt:
        mStillRunning = False;

    finally:
        save_filter_info()
        mStillRunning = False;
        mUserInputThread.join();
        process.kill()
