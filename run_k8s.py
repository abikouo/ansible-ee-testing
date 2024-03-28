#!/usr/bin/python
"""Script to request new aws session using ansible_core_ci_key."""

import copy
import os
import re
import subprocess
import sys
from argparse import ArgumentParser
from pathlib import PosixPath
from tempfile import NamedTemporaryFile
import json
import logging
import yaml


logger = logging.getLogger("Main")
logging.basicConfig(level=logging.DEBUG)


def build_kubeconfig():
    command = "kubectl get nodes -o json"
    logger.debug("Running command => '%s'" % command)
    cmd = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, shell=True)
    stdout, stderr = cmd.communicate()
    if cmd.returncode != 0:
        logger.error(f"[ERR] Command '{command}' failed with '{stderr}'")
        sys.exit(1)

    nodes = json.loads(stdout)
    internal_ip = [addr["address"] for addr in nodes["items"][0]["status"]["addresses"] if addr["type"] == "InternalIP"][0]
    logger.info("Kubernetes node InternalIP = %s" % internal_ip)
    # Read existing config
    with open(os.path.expanduser("~/.kube/config")) as fd:
        config = yaml.safe_load(fd)
    for cluster in config["clusters"]:
        cluster["cluster"]["server"] = f"https://{internal_ip}:6443"

    yaml_file = NamedTemporaryFile(mode="w", suffix=".yaml", delete=False)
    yaml.dump(config, yaml_file, default_flow_style=False, explicit_start=True)
    yaml_file.close()
    return yaml_file.name


def build_playbook(yaml_file, role_name, variables):
    data = {
        "hosts": "localhost",
        "gather_facts": False,
        "roles": [
            {"role": role_name},
        ],
    }
    if variables:
        data.update({"vars": variables})
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


def run(eei_image, collection_path, targets, use_stdout, allow_slow):

    # variables = {}
    # if vars_file:
    #     with open(vars_file, "r") as fd:
    #         variables.update(yaml.safe_load(fd))
    # print("Variables => ", variables)

    if targets:
        targets = [t.strip() for t in targets.split(",") if t]

    config_file_name = build_kubeconfig()
    kubeconfig_path = "/.kube"
    set_envs = [
        "--senv ANSIBLE_ROLES_PATH=/roles",
        f"--senv K8S_AUTH_KUBECONFIG={kubeconfig_path}/{os.path.basename(config_file_name)}"
    ]
    path = PosixPath(collection_path) / PosixPath("tests/integration/targets")
    results = []
    for target in path.iterdir():
        if skip_target(target, targets, allow_slow):
            continue

        yaml_file = NamedTemporaryFile(mode="w", suffix=".yaml", delete=False)

        command = []
        runme = target / PosixPath("runme.sh")
        if runme.exists():
            logger.info(f"target -- {target.stem} -- contains 'runme.sh' file.")
            command = [
                "docker",
                "run",
                "--network kind",
                f"--env K8S_AUTH_KUBECONFIG={kubeconfig_path}/{os.path.basename(config_file_name)}",
                f"-v {path.expanduser()}:/targets",
                f"-v {os.path.dirname(config_file_name)}:{kubeconfig_path}:Z",
                f"-w /targets/{target.stem}",
                eei_image,
                "./runme.sh"
            ]
        else:
            build_playbook(yaml_file, target.stem, variables={})
            command = [
                "ansible-navigator",
                "run",
                yaml_file.name,
                "-v",
                "--ee true",
                f"--eei {eei_image}",
                "--ce docker",
                f"--eev {path.expanduser()}:/roles:Z",
                f"--eev {os.path.dirname(config_file_name)}:{kubeconfig_path}:Z",
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
    os.remove(config_file_name)
    print("\n".join(results))


def main() -> None:
    parser = ArgumentParser(
        description="Generate playbook and run using execution environment."
    )
    parser.add_argument("--eei", required=True, help="Execution environment image.")
    parser.add_argument(
        "--collection-path", required=True, help="Path to the collection to test."
    )
    parser.add_argument("--targets", help="Comma-separated list of targets to test.")
    parser.add_argument(
        "--use-stdout", action="store_true", help="Use stdout for the execution output"
    )
    parser.add_argument(
        "--allow-slow", action="store_true", help="Allow running too long targets"
    )
    logger.setLevel(logging.DEBUG)

    args = parser.parse_args()

    run(
        eei_image=args.eei,
        collection_path=args.collection_path,
        targets=args.targets,
        use_stdout=args.use_stdout,
        allow_slow=args.allow_slow,
    )


if __name__ == "__main__":
    main()
