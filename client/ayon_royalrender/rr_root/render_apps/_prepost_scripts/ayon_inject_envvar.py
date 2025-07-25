import argparse
import json
import os
import subprocess
import sys
import tempfile
import uuid
from datetime import datetime

mod_dir = os.path.join(os.environ["RR_ROOT"], "SDK", "External", "Python")
if mod_dir not in sys.path:
    sys.path.append(mod_dir)
import rr_python_utils.connection as rr_connect


logs = []


class InjectEnvironment:
    """Creates rrEnv file.

    RR evnList has limitation on 2000 characters, which might not be enough.
    This script should be triggered by render jobs that were published from
    Ayon, it uses .json metadata to parse context and required Ayon launch
    environments to generate environment variable file for particular context.

    This file is converted into rrEnv file.

    Render job already points to non-existent location which got filled only
    by this process. (Couldn't figure way how to attach new file to existing
    job.)

    Expected set environments on RR worker:
    - AYON_SERVER_URL
    - AYON_API_KEY - API key to Ayon server, most likely from service account
    - AYON_EXECUTABLE - locally accessible path for `ayon_console`
    (could be removed if it would be possible to have it in renderApps config
    and to be accessible from there as there it is required for publish jobs).
    - AYON_FILTER_ENVIRONMENTS - potential black list of unwanted environment
    variables (separated by ';') - will be filtered out from created .rrEnv.

    Ayon submission job must be adding this line to .xml submission file:
    <SubmitterParameter>PPAyoninjectenvvar=1~1</SubmitterParameter>

    Scripts logs into folder with metadata json - could be removed if there
    is a way how to log into RR output.

    """

    def __init__(self):
        self.meta_dir = None
        self.tcp = self.tcp_connect()

    def tcp_connect(self):
        tcp = rr_connect.server_connect(user_name=None)
        tcp.configGetGlobal()
        if tcp.errorMessage():
            print(tcp.errorMessage())
            raise ConnectionError(tcp.errorMessage())
        return tcp

    def inject(self):
        # TODO logging only in RR not to file?
        logs.append("InjectEnvironment starting")
        meta_dir = self._get_metadata_dir()
        self.meta_dir = meta_dir
        envs = self._get_job_environments()

        if not envs.get("AYON_RENDER_JOB"):
            logs.append("Not a ayon render job, skipping.")
            return

        self._check_launch_environemnt()

        context = self._get_context()

        logs.append("context {}".format(context))
        executable = self._get_executable()

        logs.append("executable {}".format(executable))

        extracted_env = self._extract_environments(executable, context)

        rrEnv_path = self._create_rrEnv(meta_dir, extracted_env)
        print(f"Ayon job environment exported to rrEnv file:\n{rrEnv_path}")
        logs.append(f"InjectEnvironment ending, rrEnv file {rrEnv_path}")

    def _get_metadata_dir(self):
        """Get folder where metadata.json and renders should be produced."""
        sys.path.append(os.environ["RR_ROOT"] + "/render_apps/scripts")
        job = self._get_job()

        new_path = job.imageDir

        logs.append(f"_get_metadata_dir::{new_path}")
        return new_path

    def _check_launch_environemnt(self):
        required_envs = ["AYON_SERVER_URL", "AYON_API_KEY", "AYON_EXECUTABLE"]
        missing = []
        for key in required_envs:
            if not os.environ.get(key):
                missing.append(key)

        if missing:
            msg = (
                f"Required environment variable missing: '{','.join(missing)}"
            )
            logs.append(msg)
            raise RuntimeError(msg)

    def _get_context(self):
        envs = self._get_job_environments()
        return {
            "project": envs["AYON_PROJECT_NAME"],
            "asset": envs["AYON_FOLDER_PATH"],
            "task": envs["AYON_TASK_NAME"],
            "app": envs["AYON_APP_NAME"],
            "envgroup": "farm",
        }

    def _get_job(self):
        logs.append("get_jobs")
        parser = argparse.ArgumentParser()
        parser.add_argument("-jid")
        parser.add_argument("-authStr")
        args = parser.parse_args()

        logs.append("jid:{}".format(int(args.jid)))

        if not self.tcp.jobList_GetInfo(int(args.jid)):
            print("Error jobList_GetInfo: " + self.tcp.errorMessage())
            sys.exit()
        job = self.tcp.jobs.getJobSend(int(args.jid))
        self.tcp.jobs.setPathTargetOS(job.sceneOS)

        return job

    def _get_job_environments(self):
        """Gets environments set on job.

        It seems that it is not possible to query "rrEnvList" on job directly,
        it must be parsed from .json document.
        """
        job = self._get_job()
        env_list = job.customData_Str("rrEnvList")
        envs = {}
        for env in env_list.split("~~~"):
            key, value = env.split("=")
            envs[key] = value

        return envs

    def _get_executable(self):
        # rr_python_utils.cache.get_rr_bin_folder()  # TODO maybe useful
        return os.environ["AYON_EXECUTABLE"]

    def _get_launch_environments(self):
        """Enhances environemnt with required for Ayon to be launched."""
        job_envs = self._get_job_environments()
        ayon_environment = {
            "AYON_SERVER_URL": os.environ["AYON_SERVER_URL"],
            "AYON_API_KEY": os.environ["AYON_API_KEY"],
            "AYON_BUNDLE_NAME": job_envs["AYON_BUNDLE_NAME"],
        }
        logs.append("Ayon launch environments:: {}".format(ayon_environment))
        environment = os.environ.copy()
        environment.update(ayon_environment)
        return environment

    def _get_export_url(self):
        """Returns unique path with extracted env variables from Ayon."""
        temp_file_name = "{}_{}.json".format(
            datetime.utcnow().strftime("%Y%m%d%H%M%S%f"), str(uuid.uuid1())
        )
        export_url = os.path.join(tempfile.gettempdir(), temp_file_name)
        return export_url

    def _extract_environments(self, executable, context):
        # tempfile.TemporaryFile cannot be used because of locking
        export_url = self._get_export_url()

        args = [executable, "--headless", "extractenvironments", export_url]

        if all(context.values()):
            for key, value in context.items():
                args.extend(["--{}".format(key), value])

        environments = self._get_launch_environments()

        logs.append("Running:: {}".format(args))
        proc = subprocess.Popen(
            args,
            env=environments,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        output, error = proc.communicate()

        if not os.path.exists(export_url):
            logs.append("output::{}".format(output))
            logs.append("error::{}".format(error))
            raise RuntimeError("Extract failed with {}".format(error))

        with open(export_url) as json_file:
            return json.load(json_file)

    def _create_rrEnv(self, meta_dir, extracted_env):
        """Create rrEnv.rrEnv file in metadata folder that render job points"""
        filter_out = os.environ.get("AYON_FILTER_ENVIRONMENTS")
        filter_envs = set()
        if filter_out:
            filter_envs = set(filter_out.split(";"))

        lines = []
        for key, value in extracted_env.items():
            if key in filter_envs:
                continue

            line = f"{key} = {value}"
            lines.append(line)

        rrenv_path = os.path.join(meta_dir, "rrEnv.rrEnv")

        with open(rrenv_path, "w") as fp:
            fp.writelines(s + "\n" for s in lines)

        return os.path.normpath(rrenv_path)


if __name__ == "__main__":
    try:
        tmpdir = None
        injector = InjectEnvironment()

        injector.inject()
        tmpdir = injector.meta_dir
    except Exception as exp:
        msg = f"Error happened::{str(exp)}"
        raise RuntimeError(msg)

    finally:
        if tmpdir is None:
            temp_file = tempfile.NamedTemporaryFile(delete=False)
            log_path = temp_file.name
        else:
            log_path = os.path.join(tmpdir, "log.txt")

        print(f"Creating log at::{log_path}")
        with open(log_path, "a") as fp:
            fp.writelines(s.replace("\\r\\n", "\n") + "\n" for s in logs)


