# The image of the evaluation Job: suite, gate, compare, drift and coverage.
# Build from the repository root, with eval/ as the build context:
#   docker build -f eval/Dockerfile --build-arg GIT_SHA=<commit id> -t rahbar-eval eval
FROM python:3.12-slim

RUN groupadd --system --gid 10001 app \
    && useradd --system --uid 10001 --gid app --no-create-home app

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Only what the suite needs: no results, traces, labels or old A2 evaluation files.
COPY suite/ suite/
COPY run_suite.py gate.py compare.py coverage.py drift.py stochasticity.py judge_agreement.py ./
COPY golden.jsonl module_pairs.jsonl live_queries.jsonl ./

# The image has no git, so the commit it was built from is passed in. It goes into every
# result file.
ARG GIT_SHA=""
ENV GIT_SHA=${GIT_SHA} \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

# A numeric user, so that Kubernetes can check runAsNonRoot.
USER 10001

CMD ["python", "run_suite.py", "--help"]
