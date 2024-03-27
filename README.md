# Testing cloud collection Using Execution environment


## How to build execution environment

In this context we are going to test AAP 2.5 using execution environment base image ``registry-proxy.engineering.redhat.com/rh-osbs/ansible-automation-platform-25-ee-supported-rhel9:latest``.
From RedHat VPN run the following command:

```shell
docker pull registry-proxy.engineering.redhat.com/rh-osbs/ansible-automation-platform-25-ee-supported-rhel9:latest
```

Then build execution environment using the following command:

```shell
ansible-builder build -f ./execution-environment.yml --container-runtime docker -t <image_tag_name> --no-cache -v 3
```


here the content of my execution environment file

```yaml
---
version: 3

dependencies:
  galaxy: requirements.yml
  python: requirements.txt
  system: bindep.txt
  ansible_core:
    package_pip: ansible-core==2.16.4
  ansible_runner:
    package_pip: ansible-runner==2.3.6
  python_interpreter:
    package_system: "python3.11"
    python_path: "/usr/bin/python3.11"

images:
  base_image:
    name: registry-proxy.engineering.redhat.com/rh-osbs/ansible-automation-platform-25-ee-supported-rhel9:latest

options:
  package_manager_path: /usr/bin/microdnf

additional_build_files:
  - src: ansible.cfg
    dest: configs

additional_build_steps:
  append_base: |
    RUN echo "Target architecture is: $TARGETARCH"

    # Configure Terraform Repo
    RUN curl -o /etc/yum.repos.d/hashicorp.repo https://rpm.releases.hashicorp.com/RHEL/hashicorp.repo

  prepend_galaxy: |
    COPY _build/configs/ansible.cfg /etc/ansible/ansible.cfg
    ENV ANSIBLE_GALAXY_SERVER_AUTOMATION_HUB_URL=https://console.redhat.com/api/automation-hub/content/published/
    ENV ANSIBLE_GALAXY_SERVER_AUTOMATION_HUB_AUTH_URL=https://sso.redhat.com/auth/realms/redhat-external/protocol/openid-connect/token
    ARG ANSIBLE_GALAXY_SERVER_AUTOMATION_HUB_TOKEN

  append_final: |
    # Use Python 3.11 as default
    RUN rm -f /bin/python3
    RUN ln -s /bin/python3.11 /bin/python3

    # Output collections list for debugging
    RUN ansible-galaxy collection list

    # Display terraform version
    RUN terraform version
```


## How to run integration test targets

I first set the collection version tag to the expected version.
For example, for ``amazon.aws`` collection, run the following:

```shell
cd <path to collection> && git checkout x.x.x
```

The [run.py](https://github.com/abikouo/ansible-ee-testing/blob/main/run.py) is used to run the integration tests targets as follow:

```shell
# the tool is forcing ansible-navigator to use docker as container runtime, this can be easily updated
python ./run.py --eei <image_tag_name> --collection-path ~/.ansible/collections/ansible_collections/amazon/aws --vars-file vars.yaml
```

with ``vars.yaml``

```yaml
output_dir: "/tmp"
aws_access_key: xxxx # aws access key
aws_secret_key: xxxx # aws secret key
aws_region: us-east-1
```