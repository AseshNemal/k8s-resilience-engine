"""Member 4 Development Lab: an interactive, local-only view of the recommendation and validation pipeline.

DEVELOPMENT ONLY, OFFLINE. The lab serves the 17 controlled scenarios of
``tests/support/member4_scenarios.py`` through the real Member 4 evaluation pipeline
(``live_pipeline.evaluate_recommendation``) and the real validation pipeline (``validation.ValidationRun``)
driven by the scripted in-memory test cluster. It reads no cluster, no kubeconfig and no live evidence,
it applies nothing, and it is never mounted by ``app.main`` or the Docker Desktop extension.

Run it from the repository root:

    python -m experiments.member4_dev_lab

Then open http://127.0.0.1:8741 (override with DEV_LAB_PORT).
"""
