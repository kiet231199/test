import re


###############################################################################
#                              GLOBAL VARIABLES                                #
###############################################################################

COMPARISON_PATTERN = re.compile(r"^(>=|<=|>|<)\s*(.+)$")
INTERVAL_PATTERN   = re.compile(r"^([\[\(])\s*(.+?)\s*;\s*(.+?)\s*([\]\)])$")


###############################################################################
#                               LOCAL FUNCTIONS                               #
###############################################################################

def _is_number(value: str) -> bool:
    """
    Return True when value can be converted to a float.
    """
    try:
        float(value)
        return True
    except ValueError:
        return False


def _numeric_expression(operator: str, bound: str) -> str:
    """
    Build a bash test expression comparing ${value} against a numeric bound.
    """
    return f'[[ $(echo "${{value}} {operator} {bound}" | bc -l) -eq 1 ]]'


def _string_expression(text: str) -> str:
    """
    Build a bash test expression comparing ${value} against a literal string.
    """
    return f'[[ "${{value}}" == "{text}" ]]'


def _parse_comparison(criteria: str) -> str:
    """
    Parse a comparison criteria (e.g. >=29) into a numeric bash expression.
    """
    match = COMPARISON_PATTERN.match(criteria)

    if not match:
        raise ValueError(f"Malformed comparison criteria: '{criteria}'")

    operator = match.group(1)
    bound    = match.group(2).strip()

    if not _is_number(bound):
        raise ValueError(f"Comparison bound is not numeric: '{bound}'")

    return _numeric_expression(operator, bound)


def _parse_interval(criteria: str) -> str:
    """
    Parse an interval criteria (e.g. [200; 300)) into a combined bash
    expression. Square brackets are inclusive, parentheses are exclusive.
    """
    match = INTERVAL_PATTERN.match(criteria)

    if not match:
        raise ValueError(f"Malformed interval criteria: '{criteria}'")

    left_bracket  = match.group(1)
    lower_bound   = match.group(2).strip()
    upper_bound   = match.group(3).strip()
    right_bracket = match.group(4)

    if not lower_bound or not upper_bound:
        raise ValueError(f"Interval is missing a bound: '{criteria}'")

    if not _is_number(lower_bound):
        raise ValueError(f"Interval lower bound is not numeric: '{lower_bound}'")

    if not _is_number(upper_bound):
        raise ValueError(f"Interval upper bound is not numeric: '{upper_bound}'")

    lower_operator = ">=" if left_bracket == "[" else ">"
    upper_operator = "<=" if right_bracket == "]" else "<"

    lower_expression = _numeric_expression(lower_operator, lower_bound)
    upper_expression = _numeric_expression(upper_operator, upper_bound)

    return f"{lower_expression} && {upper_expression}"


def parse_criteria(criteria: str) -> str:
    """
    Parse a metric criteria string and return a bash condition string that
    evaluates truthy when ${value} satisfies the criteria.

    The returned expression is designed to be placed directly after an 'if',
    for example: if <EXPR>; then ...
    """
    if criteria is None:
        raise ValueError("Criteria is empty")

    criteria = criteria.strip()

    if not criteria:
        raise ValueError("Criteria is empty")

    # Interval notation, e.g. [200; 300), (200; 300], ...
    if criteria[0] in "[(" or criteria[-1] in "])":
        return _parse_interval(criteria)

    # Comparison operators, e.g. >=29, <300, ...
    if COMPARISON_PATTERN.match(criteria):
        return _parse_comparison(criteria)

    # Plain numeric equality, e.g. 224, 4.1, 0
    if _is_number(criteria):
        return _numeric_expression("==", criteria)

    # Plain string equality, e.g. Baseline
    return _string_expression(criteria)
