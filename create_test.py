#!/usr/bin/env python3

import argparse
import subprocess
import sys
from pathlib import Path

from scripts import log
from scripts.specification import create_test_from_excel


###############################################################################
#                              GLOBAL VARIABLES                                #
###############################################################################

OUT_DIR          = "out"
REMOTE_IP_PREFIX = "192.168.5"


###############################################################################
#                               LOCAL FUNCTIONS                                #
###############################################################################

def run_ssh_command(ip_last: int, command: str, timeout: int = 10) -> str:
    """
    Run command on remote board by SSH.
    """
    host = f"root@{REMOTE_IP_PREFIX}.{ip_last}"

    ssh_cmd = [
        "ssh",
        "-o", "BatchMode=yes",
        "-o", "ConnectTimeout=5",
        host,
        command,
    ]

    try:
        result = subprocess.run(
            ssh_cmd,
            stdout  = subprocess.PIPE,
            stderr  = subprocess.PIPE,
            text    = True,
            timeout = timeout,
            check   = False,
        )
    except subprocess.TimeoutExpired:
        raise RuntimeError(f"SSH timeout when connecting to {host}")

    if result.returncode != 0:
        raise RuntimeError(
            f"Cannot access remote board: {host}\n"
            f"Command: {' '.join(ssh_cmd)}\n"
            f"stderr:\n{result.stderr.strip()}"
        )

    return result.stdout.strip()


def get_remote_nfs_root(ip_last: int) -> Path:
    """
    Get NFS root directory of remote board.

    The remote board must boot rootfs from NFS.
    """
    run_ssh_command(ip_last, "true")

    df_output = run_ssh_command(ip_last, "df -P /")

    lines = [line.strip() for line in df_output.splitlines() if line.strip()]

    if len(lines) < 2:
        raise RuntimeError(
            "Cannot parse remote df output.\n"
            f"df output:\n{df_output}"
        )

    fields = lines[1].split()

    if len(fields) < 6:
        raise RuntimeError(
            "Unexpected remote df output format.\n"
            f"df output:\n{df_output}"
        )

    filesystem  = fields[0]
    mount_point = fields[-1]

    if mount_point != "/":
        raise RuntimeError(
            "Remote df output is not root filesystem.\n"
            f"Parsed mount point: {mount_point}\n"
            f"df output:\n{df_output}"
        )

    if ":" not in filesystem:
        raise RuntimeError(
            "Remote root filesystem does not look like NFS.\n"
            f"Parsed filesystem: {filesystem}\n"
            f"df output:\n{df_output}"
        )

    nfs_root = filesystem.split(":", 1)[1]

    if not nfs_root.startswith("/"):
        raise RuntimeError(
            "Invalid NFS root path parsed from remote df output.\n"
            f"Parsed NFS root: {nfs_root}\n"
            f"df output:\n{df_output}"
        )

    return Path(nfs_root)


def find_work_dir(ip_last: int) -> str:
    """
    Find WORK_DIR path on remote board.

    Example:
        Local PWD:
            /data3/tftpboot/remote_machine/test_program

        NFS root:
            /data3/tftpboot/remote_machine

        WORK_DIR:
            /test_program
    """
    remote_nfs_root = get_remote_nfs_root(ip_last)

    try:
        local_pwd = Path.cwd().resolve(strict=True)
    except FileNotFoundError:
        raise RuntimeError(f"Current working directory does not exist: {Path.cwd()}")

    try:
        nfs_root = remote_nfs_root.resolve(strict=True)
    except FileNotFoundError:
        raise RuntimeError(
            "Remote NFS root parsed from target board does not exist on local machine.\n"
            f"Remote NFS root from board df: {remote_nfs_root}\n"
            f"Local PWD: {local_pwd}"
        )

    try:
        relative_path = local_pwd.relative_to(nfs_root)
    except ValueError:
        raise RuntimeError(
            "Local PWD is not inside the remote NFS root directory.\n"
            f"Remote NFS root from board df: {nfs_root}\n"
            f"Local PWD: {local_pwd}"
        )

    if str(relative_path) == ".":
        return "/"

    return "/" + relative_path.as_posix()


def main():
    parser = argparse.ArgumentParser(
        description = "Create GStreamer test program directory from Excel specification"
    )

    parser.add_argument(
        "specification",
        help = "Input Excel specification file, for example: Specification.xlsx",
    )

    parser.add_argument(
        "-i",
        "--ip",
        required = True,
        type     = int,
        help     = "Last number of remote board IP address",
    )

    args = parser.parse_args()

    if args.ip < 1 or args.ip > 254:
        log.error(f"Invalid IP last number: {args.ip}")
        sys.exit(1)

    spec_file = Path(args.specification)
    out_dir   = Path(OUT_DIR)

    try:
        work_dir    = find_work_dir(args.ip)
        pc_work_dir = str(Path.cwd().resolve(strict=True))

        log.info(f"WORK_DIR = {work_dir}")

        create_test_from_excel(
            spec_file = spec_file,
            out_dir   = out_dir,
            work_dir  = work_dir,
            pc_work_dir = pc_work_dir,
        )
    except Exception as e:
        log.error(e)
        sys.exit(1)


if __name__ == "__main__":
    main()
