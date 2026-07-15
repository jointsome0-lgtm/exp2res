# K8s Playbook — information architecture

Design note by Vera Example for her public runbook project
`vera-example/k8s-playbook`. Demo corpus content; every detail invented.

Decision: organize runbooks by failure symptom, not by component. A
reader arrives with "pods keep restarting", not with "etcd".

Target: twelve runbooks for the first complete pass.

Drafted so far: kubectl troubleshooting, etcd backup, ingress TLS.

Open question: where do cluster-upgrade notes live — under a symptom or
in a lifecycle section of their own?
