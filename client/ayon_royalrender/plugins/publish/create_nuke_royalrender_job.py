# -*- coding: utf-8 -*-
"""Submitting render job to RoyalRender."""

from ayon_royalrender import lib


class CreateNukeRoyalRenderJob(lib.BaseCreateRoyalRenderJob):
    """Creates separate rendering job for Royal Render"""

    label = "Create Nuke Render job in RR"
    hosts = ["nuke"]
    families = ["render", "prerender"]
    targets = ["local"]

    def process(self, instance):
        super(CreateNukeRoyalRenderJob, self).process(instance)
        # allow skip to
        if not instance.data.get("farm"):
            self.log.info("Skipping local instance.")
            return
        # redefinition of families
        product_base_type = instance.data.get("productBaseType")
        if not product_base_type:
            product_base_type = instance.data["productType"]
        if "render" in product_base_type:
            instance.data["productType"] = "write"
            instance.data["productBaseType"] = "write"
            instance.data["family"] = "write"
            instance.data["families"].insert(0, "render2d")
        elif "prerender" in product_base_type:
            instance.data["productType"] = "write"
            instance.data["productBaseType"] = "write"
            instance.data["family"] = "write"
            instance.data["families"].insert(0, "prerender")

        jobs = self.create_jobs(instance)
        for job in jobs:
            job = self.update_job_with_host_specific(instance, job)
            instance.data["rrJobs"].append(job)

    def update_job_with_host_specific(self, instance, job):
        # INFECTED Nuke Version
        nuke_version = instance.context.data.get("hostVersion")
        job.Software = "Nuke"
        job.Version = nuke_version

        return job

    def create_jobs(self, instance):
        """Nuke creates multiple RR jobs - for baking etc."""
        # get output path
        render_path = instance.data["path"]
        script_path = self.scene_path
        node = instance.data["transientData"]["node"]

        # main job
        jobs = [self.get_job(instance, script_path, render_path, node.name())]

        for baking_script in instance.data.get("bakingNukeScripts", []):
            render_path = baking_script["bakeRenderPath"]
            script_path = baking_script["bakeScriptPath"]
            exe_node_name = baking_script["bakeWriteNodeName"]
            single = True
            jobs.append(
                self.get_job(
                    instance, script_path, render_path, exe_node_name, single
                )
            )

        return jobs
