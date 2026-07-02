import shlex

from scripts.metrics import RESULT_FILE_NAME


###############################################################################
#                              SCRIPT TEMPLATE                                #
###############################################################################

SCRIPT_TEMPLATE = """#!/bin/bash
# Generated from specification

{test_information}

{run_function}

{check_function}

for arg; do "$arg"; done
"""

ENVIRONMENT_TEMPLATE = """export XDG_RUNTIME_DIR=/run
    export GST_DEBUG_NO_COLOR=1
    export WORK_DIR={work_dir}"""

CHECK_ENVIRONMENT_TEMPLATE = "export WORK_DIR={pc_work_dir}"

RUN_PIPELINE_TEMPLATE = """# Run pipeline
    pipeline={pipeline}
    echo ${{pipeline}} && eval ${{pipeline}}
    echo $? > {result_file}"""

PERMISSION_TEMPLATE = """# Grant permission
    output={output}
    if [ -n "${{output}}" ]; then chmod -R 777 ${{output}}; fi"""

RUN_FUNCTION_TEMPLATE = """# This function must be run on target board
# Usage: ./script.sh run
run() {{
    {setup_environment}

    {run_pipeline}

    {grand_permission}
}}"""

CHECK_FUNCTION_TEMPLATE = """# This function is run on PC
# Usage: ./script.sh check
check() {{
    {check_body}
}}"""


###############################################################################
#                               LOCAL FUNCTIONS                               #
###############################################################################

def render_run_pipeline(pipeline: str) -> str:
    if not pipeline:
        return ""

    pipeline = shlex.quote(pipeline)

    return RUN_PIPELINE_TEMPLATE.format(
        pipeline    = pipeline,
        result_file = shlex.quote(RESULT_FILE_NAME),
    )


def render_permission(output: str) -> str:
    if not output:
        return ""

    output = shlex.quote(output)

    return PERMISSION_TEMPLATE.format(
        output = output,
    )


def render_run_function(pipeline: str, output: str, work_dir: str):
    """
    Render run() function using template.
    """
    output   = output.strip()
    pipeline = pipeline.strip()

    if not pipeline:
        raise ValueError("Pipeline is empty")

    run_pipeline_template = render_run_pipeline(pipeline)
    permission_template   = render_permission(output)

    return RUN_FUNCTION_TEMPLATE.format(
        setup_environment = ENVIRONMENT_TEMPLATE.format(work_dir = shlex.quote(work_dir)),
        run_pipeline      = run_pipeline_template,
        grand_permission  = permission_template,
    )


def render_check_function(metrics_with_expr: list, pc_work_dir: str) -> str:
    """
    Render check() function using template.

    metrics_with_expr is a list of tuples:
        (metric_name, extract_bash, condition_bash)

    pc_work_dir is the local NFS-backed program directory used on the PC.
    """
    lines = [
        CHECK_ENVIRONMENT_TEMPLATE.format(
            pc_work_dir = shlex.quote(pc_work_dir),
        ),
        "",
        "result=PASSED",
    ]

    for metric_name, extract_bash, condition_bash in metrics_with_expr:
        block = "\n".join([
            "",
            "# Check {name}".format(name = metric_name),
            extract_bash,
            "if {condition}; then".format(condition = condition_bash),
            '    echo "[  OK   ] {name} = ${{value}}"'.format(name = metric_name),
            "else",
            '    echo "[  NG   ] {name} = ${{value}}"'.format(name = metric_name),
            "    result=FAILED",
            "fi",
        ])
        lines.append(block)

    lines.append("")
    lines.append("echo ${result}")

    check_body = "\n".join(lines)
    check_body = check_body.replace("\n", "\n    ")

    return CHECK_FUNCTION_TEMPLATE.format(
        check_body = check_body,
    )


def render_bash_script(
    test_information: str,
    pipeline: str,
    output: str,
    work_dir: str,
    pc_work_dir: str,
    check_metrics: list,
):
    """
    Create final script.sh content using templates.
    """
    run_function = render_run_function(
        pipeline = pipeline,
        output   = output,
        work_dir = work_dir,
    )

    check_function = render_check_function(
        metrics_with_expr = check_metrics,
        pc_work_dir       = pc_work_dir,
    )

    return SCRIPT_TEMPLATE.format(
        test_information = test_information,
        run_function     = run_function,
        check_function   = check_function,
    )
