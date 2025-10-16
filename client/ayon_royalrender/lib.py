# -*- coding: utf-8 -*-
"""Submitting render job to RoyalRender."""
import os
import re
from datetime import datetime
from enum import Enum
from typing import Optional, Any, Dict

import pyblish.api

from ayon_core.lib import (
    BoolDef,
    NumberDef,
    is_in_tests,
)
from ayon_royalrender.api import Api as rrApi
from ayon_royalrender.rr_job import (
    RREnvList,
    RRJob,
    SubmitterParameter,
    get_rr_platform,
)
from ayon_core.pipeline import AYONPyblishPluginMixin
from ayon_core.pipeline.publish import KnownPublishError
from ayon_core.pipeline.publish.lib import get_published_workfile_instance


class BaseCreateRoyalRenderJob(
    pyblish.api.InstancePlugin, AYONPyblishPluginMixin
):
    """Creates separate rendering job for Royal Render"""

    label = "Create Nuke Render job in RR"
    order = pyblish.api.IntegratorOrder + 0.1
    hosts = ["nuke"]
    families = ["render", "prerender"]
    targets = ["local"]
    optional = True

    priority = 50
    chunk_size = 1
    concurrent_tasks = 1
    use_gpu = True
    use_published = True
    auto_delete = True

    @classmethod
    def get_attribute_defs(cls):
        return [
            NumberDef(
                "priority",
                label="Priority",
                default=cls.priority,
                decimals=0
            ),
            NumberDef(
                "chunk",
                label="Frames Per Task",
                default=cls.chunk_size,
                decimals=0,
                minimum=1,
                maximum=1000
            ),
            NumberDef(
                "concurrency",
                label="Concurrency",
                default=cls.concurrent_tasks,
                decimals=0,
                minimum=1,
                maximum=10
            ),
            BoolDef(
                "use_gpu",
                default=cls.use_gpu,
                label="Use GPU"
            ),
            BoolDef(
                "suspend_publish",
                default=False,
                label="Suspend publish"
            ),
            BoolDef(
                "use_published",
                default=cls.use_published,
                label="Use published workfile",
            ),
            BoolDef(
                "auto_delete",
                default=cls.auto_delete,
                label="Cleanup temp renderfolder",
            ),
        ]

    def __init__(self, *args, **kwargs):
        self._rr_root = None
        self.scene_path = None
        self.job = None
        self.submission_parameters = None
        self.rr_api = None

    def process(self, instance):
        if not instance.data.get("farm"):
            self.log.info("Skipping local instance.")
            return

        instance.data["attributeValues"] = self.get_attr_values_from_data(
            instance.data
        )

        # add suspend_publish and auto_delete  attributeValue to instance data
        instance.data["suspend_publish"] = instance.data["attributeValues"][
            "suspend_publish"
        ]
        instance.data["auto_delete"] = instance.data["attributeValues"][
            "auto_delete"
        ]
        instance.data["priority"] = instance.data["attributeValues"][
            "priority"
        ]

        context = instance.context

        self._rr_root = instance.data.get("rr_root")
        self.log.debug(self._rr_root)
        if not self._rr_root:
            raise KnownPublishError(
                (
                    "Missing RoyalRender root. "
                    "You need to configure RoyalRender module."
                )
            )

        self.rr_api = rrApi(self._rr_root)

        self.scene_path = context.data["currentFile"]
        if self.use_published:
            published_workfile = get_published_workfile_instance(context)

            # fallback if nothing was set
            if published_workfile is None:
                self.log.warning("Falling back to workfile")
                file_path = context.data["currentFile"]
            else:
                workfile_repre = published_workfile.data["representations"][0]
                file_path = workfile_repre["published_path"]

            self.scene_path = file_path
            self.log.info(
                "Using published scene for render {}".format(self.scene_path)
            )

        if not instance.data.get("expectedFiles"):
            instance.data["expectedFiles"] = []

        if not instance.data.get("rrJobs"):
            instance.data["rrJobs"] = []

    def get_job(
        self,
        instance,
        script_path,
        render_path,
        node_name,
        single=False,
        job_type="RENDER"
    ):
        """Get RR job based on current instance.

        Args:
            script_path (str): Path to Nuke script.
            render_path (str): Output path.
            node_name (str): Name of the render node.

        Returns:
            RRJob: RoyalRender Job instance.

        """
        anatomy = instance.context.data["anatomy"]
        start_frame = int(instance.data["frameStartHandle"])
        end_frame = int(instance.data["frameEndHandle"])
        padding = anatomy.templates_obj.frame_padding

        batch_name = os.path.basename(script_path)
        jobname = "%s - %s" % (batch_name, instance.name)
        if is_in_tests():
            batch_name += datetime.now().strftime("%d%m%Y%H%M%S")

        render_dir = os.path.normpath(os.path.dirname(render_path))
        output_filename_0 = self.pad_file_name(
            render_path, str(start_frame), padding
        )

        file_name, file_ext = os.path.splitext(
            os.path.basename(output_filename_0)
        )

        custom_attributes = []
        job_disabled = "1" if instance.data["suspend_publish"] is True else "0"
        priority = instance.data["priority"]

        submitter_parameters_job = [
            SubmitterParameter("SendJobDisabled", "1", f"{job_disabled}"),
            SubmitterParameter("Priority", "1", f"{priority}"),
        ]

        # this will append expected files to instance as needed.
        expected_files = self.expected_files(
            instance, render_path, start_frame, end_frame
        )
        instance.data["expectedFiles"].extend(expected_files)

        environment = get_instance_job_envs(instance)
        environment.update(JobType[job_type].get_job_env())
        environment = RREnvList(**environment)
        environment_serialized = environment.serialize()
        environment_serialized += r'~~~[exec] "<rrLocalBin><OsxApp rrPythonconsole>"  <rrLocalRenderScripts>ayon_inject_envvar.py <rrLocalTemp>/myDynamicEnv.allos'
        environment_serialized += r'~~~[exec] <rrLocalTemp>/myDynamicEnv.allos'

        render_dir = render_dir.replace("\\", "/")
        job = RRJob(
            Software="",
            Renderer="",
            SeqStart=int(start_frame),
            SeqEnd=int(end_frame),
            SeqStep=int(instance.data.get("byFrameStep", 1)),
            ImageFramePadding=padding,
            SeqFileOffset=0,
            Version=0,
            SceneName=script_path,
            IsActive=True,
            ImageDir=render_dir.replace("\\", "/"),
            ImageFilename=file_name,
            ImageExtension=file_ext,
            ImagePreNumberLetter="",
            ImageSingleOutputFile=single,
            SceneOS=get_rr_platform(),
            Layer=node_name,
            SceneDatabaseDir=script_path,
            CustomSHotName=jobname,
            CompanyProjectName=instance.context.data["projectName"],
            ImageWidth=instance.data.get("resolutionWidth"),
            ImageHeight=instance.data.get("resolutionHeight"),
            CustomAttributes=custom_attributes,
            SubmitterParameters=submitter_parameters_job,
            rrEnvList=environment_serialized,
            rrEnvFile=os.path.join(render_dir, "rrEnv.rrEnv"),
        )

        return job

    def update_job_with_host_specific(self, instance, job):
        """Host specific mapping for RRJob"""
        raise NotImplementedError

    def expected_files(self, instance, path, start_frame, end_frame):
        """Get expected files.

        This function generate expected files from provided
        path and start/end frames.

        It was taken from Deadline module, but this should be
        probably handled better in collector to support more
        flexible scenarios.

        Args:
            instance (Instance)
            path (str): Output path.
            start_frame (int): Start frame.
            end_frame (int): End frame.

        Returns:
            list: List of expected files.

        """
        dir_name = os.path.dirname(path)
        file = os.path.basename(path)

        expected_files = []

        if "#" in file:
            pparts = file.split("#")
            padding = "%0{}d".format(len(pparts) - 1)
            file = pparts[0] + padding + pparts[-1]

        if "%" not in file:
            expected_files.append(path)
            return expected_files

        if instance.data.get("slate"):
            start_frame -= 1

        expected_files.extend(
            os.path.join(dir_name, (file % i)).replace("\\", "/")
            for i in range(start_frame, (end_frame + 1))
        )
        return expected_files

    def pad_file_name(self, path, first_frame, padding):
        """Return output file path with #### for padding.

        RR requires the path to be formatted with # in place of numbers.
        For example `/path/to/render.####.png`

        Args:
            path (str): path to rendered image
            first_frame (str): from representation to cleany replace with #
                padding

        Returns:
            str

        """
        self.log.debug("pad_file_name path: `{}`".format(path))
        self.log.debug("padding_from_anatatomy_preset: `{}`".format(padding))
        if "%" in path:
            search_results = re.search(r"(%0)(\d)(d.)", path).groups()
            self.log.debug("_ search_results: `{}`".format(search_results))
            return int(search_results[1])
        if "#" in path:
            self.log.debug("already padded: `{}`".format(path))
            return path

        if first_frame:
            path = path.replace(str(first_frame).zfill(padding), "#" * padding)

        return path


def get_instance_job_envs(instance) -> "dict[str, str]":
    """Add all job environments as specified on the instance and context.

    Any instance `job_env` vars will override the context `job_env` vars.
    """
    # Avoid import from 'ayon_core.pipeline'
    from ayon_core.pipeline.publish import FARM_JOB_ENV_DATA_KEY

    env = {}
    for job_env in [
        instance.context.data.get(FARM_JOB_ENV_DATA_KEY, {}),
        instance.data.get(FARM_JOB_ENV_DATA_KEY, {})
    ]:
        if job_env:
            env.update(job_env)

    # Return the dict sorted just for readability in future logs
    if env:
        env = dict(sorted(env.items()))

    return env


class JobType(str, Enum):
    UNDEFINED = "undefined"
    RENDER = "render"
    PUBLISH = "publish"
    REMOTE = "remote"

    def get_job_env(self) -> Dict[str, str]:
        return {
            "AYON_PUBLISH_JOB": str(int(self == JobType.PUBLISH)),
            "AYON_RENDER_JOB": str(int(self == JobType.RENDER)),
            "AYON_REMOTE_PUBLISH": str(int(self == JobType.REMOTE)),
        }

    @classmethod
    def get(
        cls, value: Any, default: Optional[Any] = None
    ) -> "JobType":
        try:
            return cls(value)
        except ValueError:
            if default is None:
                return cls.UNDEFINED
            return default
