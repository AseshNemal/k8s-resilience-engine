# K8s Resilience Engine

A dependency-aware resilience assessment and recommendation system for Kubernetes microservices. It connects business
workflows to where pods actually run, predicts whether workflows survive a worker-node failure, recommends minimal
deployment changes, and validates them after a developer-approved apply.

```text
Workflow → Service → Pod → Node → Failure Domain
```

**Project ID:** J26-SE-311 · **Topic:** Dependency Aware Failure Domain Risk Assessment for Kubernetes Deployed Microservice Architectures
· **Stack:** FastAPI, Jinja2/HTML/CSS/vanilla JS, SQLite (extension) or PostgreSQL

## Install the Docker Desktop extension

The installer builds Resilience Engine from this checkout and adds it to the Docker Desktop sidebar. You can install it before connecting a Kubernetes cluster.

1. Install and start Docker Desktop for your operating system. On Windows, use Linux containers. The project has verified a native installation on an Apple silicon Mac. Native Windows and Linux installations await verification. The Windows helper currently supports x64 only. [Mac setup](https://docs.docker.com/desktop/setup/install/mac-install/) · [Windows setup](https://docs.docker.com/desktop/setup/install/windows-install/) · [Linux setup](https://docs.docker.com/desktop/setup/install/linux/)
2. In **Docker Desktop → Settings → Extensions**, select **Enable Docker Extensions**, clear **Allow only extensions distributed through the Docker Marketplace**, and select **Apply**. This permits installation from the locally built image. [Docker extension settings](https://docs.docker.com/extensions/settings-feedback/)
3. For the default install with tests, put Python 3.13, Node.js 22 and Go 1.26.11 on your PATH. Open a terminal in the repository root, where `requirements.txt` and `scripts/` are located. To get a fresh copy, use the following commands.

   ```sh
   git clone https://github.com/AseshNemal/k8s-resilience-engine.git
   cd k8s-resilience-engine
   ```

### macOS and Linux

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
./scripts/extension-manage.sh install
./scripts/extension-manage.sh status
```

### Windows (PowerShell or Command Prompt)

```powershell
py -3.13 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\scripts\extension-manage.cmd install
.\scripts\extension-manage.cmd status
```

Both launchers run the Python, Go and JavaScript tests, build the image, and install it. To build without the host-side tests, use `install --skip-checks` with your platform's launcher. That path needs only Docker Desktop and Python 3.9+ on the host and skips the virtual environment and dependency setup above.

### Open and update

When `status` reports the extension is running, open **Resilience Engine** in Docker Desktop’s sidebar. To assess an application, open **Connect cluster**, review the access request for your test cluster, then select the application in **Assessment**.

After pulling new code, run `./scripts/extension-manage.sh update` on macOS or Linux, or `.\scripts\extension-manage.cmd update` on Windows. Add `--dry-run` to preview an install or update. [Extension troubleshooting](docs/EXTENSION.md#troubleshooting) · [Test-cluster setup](docs/DOCKER.md)

## Team and components

Four sequential components; each consumes only the previous one's JSON output (contracts in `app/shared/schemas/`).

| Member | Registration | Component | Folder (`app/engine/`) |
|---|---|---|---|
| A.D. Perera | IT23236400 | 1. Workflow Discovery | `workflow_dependency_modeling/` |
| P.A.M. Minara | IT23154476 | 2. Deployment Graph and Placement Drift | `deployment_topology_modeling/` |
| K.V.D.Y.R. Karawita | IT23236882 | 3. Cross-Layer Failure Prediction | `failure_impact_prediction/` |
| L.A. Nemal | IT23236264 | 4. Recommendation and Validation | `recommendation_evaluation_validation/` |

## Run the standalone web app

Python 3.11+ (developed on 3.13).

```bash
python3 -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
uvicorn app.main:app --reload                        # http://127.0.0.1:8000  (API docs at /docs)
python -m pytest                                     # use `python -m`, not bare `pytest`
node --test tests/frontend/*.test.mjs extension/ui-src/*.test.mjs
```

The app reads the cluster your kubeconfig points at, read-only. It never serves sample data in place of live evidence;
if the cluster can't be read it says so. Writes (Apply, Reset) happen only on a named person's explicit request and need
`RESILIENCE_ALLOW_CLUSTER_WRITES=1`.

## Rules that matter

- Real data only at runtime; unknown facts stay `null`, never defaulted.
- No change to shared schemas or `app/main.py` without team agreement; call another member's `service.py`, never its internals.
- Never apply a recommendation automatically; never claim experimental validation before a controlled experiment has run.

## Status

Reads, prediction, ranked recommendations, approval, Apply, rollout watch, Keep/Reset and a read-only failure watch exist and
are tested on in-memory doubles. **No Apply, node failure or controlled experiment has run on a real cluster yet.**

## License

Apache License 2.0. See [LICENSE](LICENSE).

## Docs

| Read | For |
|---|---|
| `docs/CHANGES.md` | What changed recently and in whose area (start here when pulling) |
| `docs/PROJECT_CONTEXT.md` | Research context, per-member tasks, data contracts, experiment plan |
| `docs/COMPONENT_INTERFACE_OWNERSHIP.md` | Who owns which file |
| `CLAUDE.md` / `AGENTS.md` | Detailed current state and rules for coding agents |
| `docs/EXTENSION.md`, `docs/DOCKER.md` | Install, update, troubleshooting |
| `docs/MEMBER4_*.md`, `docs/RECOMMENDATION_LIVE_DATA.md`, `docs/WORKFLOW_EVIDENCE_POLICY.md` | Recommendation, validation and workflow-evidence details |
