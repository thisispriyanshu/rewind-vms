"""Deploy Rewind (API + dashboard) to AWS Lambda with a public function URL.

Zero-infrastructure-file deploy, free-tier friendly:

    python deploy/deploy.py

Reads AWS credentials and REWIND_DATABASE_URL from the environment / .env.
Idempotent: re-running updates the existing function in place.

What it does:
1. Builds a Lambda zip: linux wheels for the deps, the rewind package,
   deploy/lambda_app.py, the built dashboard, and the CockroachDB CA cert.
2. Ensures an execution role (basic logging only).
3. Creates or updates the function and a public function URL.
"""

from __future__ import annotations

import io
import json
import os
import shutil
import subprocess
import sys
import time
import zipfile
from pathlib import Path

import boto3
import botocore

REGION = os.environ.get("AWS_DEFAULT_REGION", "us-east-1")
FUNCTION_NAME = "rewind-demo"
ROLE_NAME = "rewind-lambda-role"
PYTHON_RUNTIME = "python3.12"
DEPS = ["fastapi", "mangum", "psycopg[binary]"]

ROOT = Path(__file__).resolve().parent.parent
BUILD = ROOT / "deploy" / "_build"


def build_zip() -> bytes:
    if BUILD.exists():
        shutil.rmtree(BUILD)
    BUILD.mkdir(parents=True)

    print("installing linux wheels...")
    subprocess.run(
        [
            sys.executable,
            "-m",
            "pip",
            "install",
            "--quiet",
            "--target",
            str(BUILD),
            "--platform",
            "manylinux2014_x86_64",
            "--implementation",
            "cp",
            "--python-version",
            PYTHON_RUNTIME.removeprefix("python"),
            "--only-binary=:all:",
            *DEPS,
        ],
        check=True,
    )

    shutil.copytree(ROOT / "src" / "rewind", BUILD / "rewind")
    shutil.copy(ROOT / "deploy" / "lambda_app.py", BUILD / "lambda_app.py")

    dist = ROOT / "dashboard" / "dist"
    if not dist.exists():
        raise SystemExit("dashboard/dist missing - run `npm run build` in dashboard/ first")
    shutil.copytree(dist, BUILD / "dashboard_dist")

    cert = Path(os.environ.get("APPDATA", "")) / "postgresql" / "root.crt"
    if cert.exists():
        (BUILD / "certs").mkdir()
        shutil.copy(cert, BUILD / "certs" / "root.crt")
    else:
        print("warning: no CockroachDB CA cert found; set sslrootcert yourself")

    print("zipping...")
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(BUILD.rglob("*")):
            if path.is_file() and "__pycache__" not in path.parts:
                zf.write(path, path.relative_to(BUILD).as_posix())
    data = buffer.getvalue()
    print(f"zip size: {len(data) / 1e6:.1f} MB")
    return data


def database_url_for_lambda() -> str:
    url = os.environ["REWIND_DATABASE_URL"]
    if "sslrootcert" not in url and "sslmode=verify-full" in url:
        url += "&sslrootcert=/var/task/certs/root.crt"
    return url


def ensure_role(iam) -> str:
    trust = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Principal": {"Service": "lambda.amazonaws.com"},
                "Action": "sts:AssumeRole",
            }
        ],
    }
    try:
        role = iam.create_role(
            RoleName=ROLE_NAME,
            AssumeRolePolicyDocument=json.dumps(trust),
            Description="Execution role for the Rewind demo Lambda (logs only)",
        )["Role"]
        print(f"created role {ROLE_NAME}")
        time.sleep(10)  # allow IAM propagation before Lambda assumes it
    except iam.exceptions.EntityAlreadyExistsException:
        role = iam.get_role(RoleName=ROLE_NAME)["Role"]
    iam.attach_role_policy(
        RoleName=ROLE_NAME,
        PolicyArn="arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole",
    )
    return role["Arn"]


def deploy() -> None:
    sys.path.insert(0, str(ROOT / "src"))
    from rewind.env import load_dotenv

    load_dotenv(ROOT / ".env")

    zip_bytes = build_zip()
    iam = boto3.client("iam")
    lam = boto3.client("lambda", region_name=REGION)
    role_arn = ensure_role(iam)

    config = dict(
        Runtime=PYTHON_RUNTIME,
        Role=role_arn,
        Handler="lambda_app.handler",
        Timeout=30,
        MemorySize=1024,
        Environment={"Variables": {"REWIND_DATABASE_URL": database_url_for_lambda()}},
    )

    try:
        lam.create_function(FunctionName=FUNCTION_NAME, Code={"ZipFile": zip_bytes}, **config)
        print(f"created function {FUNCTION_NAME}")
    except lam.exceptions.ResourceConflictException:
        lam.update_function_code(FunctionName=FUNCTION_NAME, ZipFile=zip_bytes)
        waiter = lam.get_waiter("function_updated_v2")
        waiter.wait(FunctionName=FUNCTION_NAME)
        lam.update_function_configuration(FunctionName=FUNCTION_NAME, **config)
        print(f"updated function {FUNCTION_NAME}")
    lam.get_waiter("function_active_v2").wait(FunctionName=FUNCTION_NAME)

    try:
        url_config = lam.create_function_url_config(FunctionName=FUNCTION_NAME, AuthType="NONE")
        print("created function URL")
    except lam.exceptions.ResourceConflictException:
        url_config = lam.get_function_url_config(FunctionName=FUNCTION_NAME)
    try:
        lam.add_permission(
            FunctionName=FUNCTION_NAME,
            StatementId="public-url",
            Action="lambda:InvokeFunctionUrl",
            Principal="*",
            FunctionUrlAuthType="NONE",
        )
    except botocore.exceptions.ClientError as err:
        if err.response["Error"]["Code"] != "ResourceConflictException":
            raise

    print(f"\nDEMO URL: {url_config['FunctionUrl']}")


if __name__ == "__main__":
    deploy()
