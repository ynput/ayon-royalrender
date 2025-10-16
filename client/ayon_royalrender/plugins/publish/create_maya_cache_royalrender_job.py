# -*- coding: utf-8 -*-
"""Submitting render job to RoyalRender."""
import os

from maya import cmds
from maya.OpenMaya import MGlobal  # noqa: F401

from ayon_royalrender import lib


class CreateMayaCacheRoyalRenderJob(lib.BaseCreateRoyalRenderJob):
    label = "Create Maya Cache job in RR"
    hosts = ["maya"]
    families = ["pointcache"]

    def update_job_with_host_specific(self, instance, job):
        job.Software = "Maya"
        job.Renderer = "RemotePublish"
        job.Version = "{0:.2f}".format(MGlobal.apiVersion() / 10000)
        job.CustomScriptFile = "<rrLocalRenderScripts>/remote_publish.py"
        workspace = instance.context.data["workspaceDir"]
        job.SceneDatabaseDir = workspace
        job.rrEnvList += f"~~~INSTANCE_IDS={instance.name}"

        return job

    def process(self, instance):
        """Plugin entry point."""
        super(CreateMayaCacheRoyalRenderJob, self).process(instance)

        if not instance.data.get("farm"):
                self.log.info("Skipping local instance.")
                return

        # append full path
        renders_dir = os.path.join(
           cmds.workspace(query=True, rootDirectory=True),
           cmds.workspace(fileRuleEntry="images")
        )

        job = self.get_job(
            instance,
            self.scene_path,
            renders_dir,
            "",
            job_type="REMOTE"
        )
        job = self.update_job_with_host_specific(instance, job)

        instance.data["rrJobs"].append(job)
