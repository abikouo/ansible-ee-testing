#!/usr/bin/python
"""Script to request new aws session using ansible_core_ci_key."""

import copy
import os
import platform
import random
import re
import subprocess
import uuid
from argparse import ArgumentParser
from pathlib import PosixPath
from tempfile import NamedTemporaryFile

import yaml


def generate_resource_prefix():
    prefix = "ansible-test-%d-%s" % (
        random.randint(10000000, 99999999),
        platform.node().split(".")[0],
    )

    return prefix


def generate_tiny_prefix():
    return uuid.uuid4().hex[0:12]


def construct_playbook(yaml_file, role_name, variables):
    tasks = [
        {
            "name": "Create temporary directory to run test.",
            "register": "tmp_path",
            "ansible.builtin.tempfile": {"suffix": ".tf", "state": "directory"},
        },
        {
            "name": f"Execute ansible role '{role_name}'",
            "vars": {"output_dir": "{{ tmp_path.path }}"},
            "block": [{"ansible.builtin.include_role": {"name": role_name}}],
            "always": [
                {
                    "name": "Delete temporary directory",
                    "ansible.builtin.file": {
                        "state": "absent",
                        "path": "{{ tmp_path.path }}",
                    },
                }
            ],
        },
    ]

    data = {
        "hosts": "localhost",
        "gather_facts": False,
        "vars": variables,
        "tasks": tasks,
    }
    yaml.dump([data], yaml_file, default_flow_style=False, explicit_start=True)


def skip_target(path, targets, allow_slow) -> bool:
    if targets and path.stem not in targets:
        return True

    aliases = path / PosixPath("aliases")
    if not aliases.exists():
        return True

    attributes = aliases.read_text().split("\n")
    if any(x.startswith("disabled") for x in attributes) or any(
        x.startswith("unsupported") for x in attributes
    ):
        print("[SKIP] ", path.stem, " disabled or unsupported")
        return True

    if allow_slow:
        return False

    for x in attributes:
        m = re.match("^time=([0-9]*)m", x)
        if m:
            if int(m.group(1)) > 5:
                print(
                    "[SKIP] ",
                    path.stem,
                    " too long, estimated time = ",
                    m.group(1),
                    "min",
                )
                return True
    return False


def run(eei_image, collection_path, vars_file, targets, use_stdout, allow_slow):
    variables = {}
    if vars_file:
        with open(vars_file, "r") as fd:
            variables.update(yaml.safe_load(fd))
    print("Variables => ", variables)

    if targets:
        targets = [t.strip() for t in targets.split(",") if t]

    # Build AWS credentials variables
    set_envs = ["--senv ANSIBLE_ROLES_PATH=/roles"]
    aws_cred_mapping = {
        "aws_access_key": "AWS_ACCESS_KEY_ID",
        "aws_secret_key": "AWS_SECRET_ACCESS_KEY",
        "security_token": "AWS_SESSION_TOKEN",
        "aws_region": "AWS_REGION",
    }
    for k, v in aws_cred_mapping.items():
        u_value = variables.get(k)
        if u_value:
            set_envs.append(f"--senv '{v}={u_value}'")

    path = PosixPath(collection_path) / PosixPath("tests/integration/targets")
    results = []
    for target in path.iterdir():
        if skip_target(target, targets, allow_slow):
            continue

        yaml_file = NamedTemporaryFile(mode="w", suffix=".yaml", delete=False)
        test_variables = copy.deepcopy(variables)
        test_variables.update(
            {
                "resource_prefix": generate_resource_prefix(),
                "tiny_prefix": generate_tiny_prefix(),
                "ansible_test": {
                    "environment": {
                        "ANSIBLE_DEBUG_BOTOCORE_LOGS": "True"
                    },
                    "module_defaults": None,
                },
            }
        )

        command = []
        runme = target / PosixPath("runme.sh")
        if runme.exists():
            yaml.dump(test_variables, yaml_file, default_flow_style=False, explicit_start=True)
            command = [
                "docker",
                "run",
                "-v",
                f"{str(target)}:/test",
                "-v",
                f"{os.path.dirname(yaml_file.name)}:/vars",
                "-w",
                "/test",
                eei_image,
                f"./runme.sh -e '@/vars/{os.path.basename(yaml_file.name)}'"
            ]
        else:
            construct_playbook(yaml_file, target.stem, test_variables)
            command = [
                "ansible-navigator",
                "run",
                yaml_file.name,
                "-v",
                "--ee true",
                f"--eei {eei_image}",
                "--ce docker",
                f"--eev {path.expanduser()}:/roles:Z",
                "-m stdout",
            ] + set_envs
        yaml_file.close()
        command = " ".join(command)
        print("\033[93m++++++++++++++++++++++++++++++++++++++++++++++++++")
        print(f"+    RUNNING TARGET => {target.stem}")
        print(f"+ {command}")
        print("++++++++++++++++++++++++++++++++++++++++++++++++++\033[00m")
        if use_stdout:
            cmd = subprocess.Popen(command, shell=True)
        else:
            cmd = subprocess.Popen(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                shell=True,
            )
        stdout, stderr = cmd.communicate()
        os.remove(yaml_file.name)
        result = f"++++ {target.stem} -> "
        if cmd.returncode == 0:
            result += "\033[92mOK\033[00m"
        else:
            if use_stdout:
                result += f"\033[91mKO\033[00m"
            else:
                stdout_f = f"{target.stem}_stdout.txt"
                with open(stdout_f, "wb") as f:
                    f.write(stdout)
                result += f"\033[91mKO\033[00m (See details {stdout_f})"
        if use_stdout:
            results.append(result)
        else:
            print(result)
    print("\n".join(results))


def main() -> None:
    parser = ArgumentParser(
        description="Generate playbook and run using execution environment."
    )
    parser.add_argument("--eei", required=True, help="Execution environment image.")
    parser.add_argument(
        "--collection-path", required=True, help="Path to the collection to test."
    )
    parser.add_argument(
        "--vars-file", help="Path to a file containing variables to set to run tests."
    )
    parser.add_argument("--targets", help="Comma-separated list of targets to test.")
    parser.add_argument(
        "--use-stdout", action="store_true", help="Use stdout for the execution output"
    )
    parser.add_argument(
        "--allow-slow", action="store_true", help="Allow running too long targets"
    )

    args = parser.parse_args()

    run(
        eei_image=args.eei,
        collection_path=args.collection_path,
        vars_file=args.vars_file,
        targets=args.targets,
        use_stdout=args.use_stdout,
        allow_slow=args.allow_slow,
    )


if __name__ == "__main__":
    main()
