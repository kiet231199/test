import sys


###############################################################################
#                              GLOBAL VARIABLES                                #
###############################################################################

COLOR_WHITE  = "\033[37m"
COLOR_YELLOW = "\033[33m"
COLOR_RED    = "\033[31m"
COLOR_CYAN   = "\033[36m"
COLOR_RESET  = "\033[0m"

TAG_WIDTH    = 7


###############################################################################
#                               LOCAL FUNCTIONS                                #
###############################################################################

def _print(color, tag, message, file = sys.stdout):
    tag = f"{tag:^{TAG_WIDTH}}"

    print(f"{color}[{tag}] {message}{COLOR_RESET}", file = file)


def info(message):
    _print(COLOR_WHITE, "INFO", message)


def warning(message):
    _print(COLOR_YELLOW, "WARNING", message)


def error(message):
    _print(COLOR_RED, "ERROR", message, file = sys.stderr)


def ok(message):
    _print(COLOR_CYAN, "OK", message)


def ng(message):
    _print(COLOR_RED, "NG", message, file = sys.stderr)
