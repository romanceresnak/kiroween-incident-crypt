"""The Scenario Lab (scenario-lab spec): 14 fixed failure scenarios, and the runner that
applies one at a time in its own namespace.

Only ``scenarios``, ``render``, ``catalog``, ``limits`` and ``client`` are imported by the
orchestrator. ``runner``, ``kube`` and ``server`` are the lab-runner's: they are the only code
that writes to Kubernetes, and the orchestrator never imports them.
"""
