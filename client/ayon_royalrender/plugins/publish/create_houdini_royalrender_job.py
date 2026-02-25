# -*- coding: utf-8 -*-
"""Submitting Houdini render job to RoyalRender."""
import os
import sys
import json
import subprocess
import tempfile
import uuid
from datetime import datetime

from ayon_royalrender import lib
from ayon_core.pipeline.farm.tools import iter_expected_files

class CreateHoudiniRoyalRenderJob(lib.BaseCreateRoyalRenderJob):
    label = "Create Houdini Render job in RR"
    hosts = ["houdini"]
    families = [
        "render", "render.farm", "render.frames_farm",
        "imagesequence", "usd.render", "karma", "usd_karma", "mantra", "redshift", "arnold"
    ]

    def update_job_with_host_specific(self, instance, job):
        """
        Updates job object with host-specific values specific to Houdini.

        This method modifies the given job object to populate Houdini-related attributes,
        such as renderer, version, camera information, and scene database directory.
        The renderer is determined by checking the instance's "renderer" or "family" data,
        and defaulting to "houdini" if neither is provided. Houdini version is fetched
        by attempting to access the Houdini Python API (hou), falling back to an
        environment variable if the API is unavailable. Camera information, if present
        in the instance data, is extracted and used to set the job's Camera attribute.
        Lastly, the scene database directory is determined using environment variables
        or scene path attributes from Houdini or the current instance.

        Args:
            instance (Any): The current instance containing relevant data for updating
                the job object.
            job (Any): The job object to be updated with host-specific values.

        Returns:
            Any: The updated job object with host-specific values populated.

        Raises:
            None.
        """
        rop = self.get_rop(instance)
        renderer = self.get_renderer(rop)
        if renderer.startswith("BRAY_HdKarma"):
            job.Software = "USD_StdA_single"  # HuskKarma
            job.rendererLicense = "/Karma" #Karma
        elif renderer.startswith("HdArnoldRendererPlugin"):
            job.Software = "Arnold-singlefile-husk"  # HuskArnold
            job.Renderer = "HtoA"  # Husk Arnold

        if renderer == "BRAY_HdKarmaXPU":
            job.CustomKarmaRenderer = "XPU"

        instance.data["renderer"] = job.Software

        job.CustomRenderSettings = self.get_rendersettings(rop)
        job.Camera = self.get_camera(instance, job.CustomRenderSettings)
        job.SceneName = str(instance.data.get("ifdFile"))

        # Houdini version
        version = os.getenv("HOUDINI_VERSION", "")
        try:
            import hou  # noqa: WPS433 (runtime import for robustness)
            version = hou.applicationVersionString()
        except Exception:
            # keep env/empty if hou not available in this context
            pass
        job.Version = version

        # Camera (optional)
        cams = instance.data.get("cameras") or instance.data.get("camera")
        if isinstance(cams, (list, tuple)) and cams:
            job.Camera = str(cams[0]).replace("'", '"')
        elif isinstance(cams, str):
            job.Camera = cams.replace("'", '"')

        # “SceneDatabaseDir”: use JOB if set, else HIP folder, else scene folder
        workspace = os.environ.get("JOB")
        if not workspace:
            try:
                import hou  # noqa: WPS433
                workspace = os.path.dirname(hou.hipFile.path())
            except Exception:
                workspace = os.path.dirname(getattr(self, "scene_path", "") or "")
        job.SceneDatabaseDir = workspace

        return job

    def _get_scene_path(self, instance):
        """
        Retrieves the scene file path using a priority-based approach.

        Tries to obtain the scene file path from the Houdini HIP file path, the current
        file path from the context, or the environment variable HIPFILE. Returns an
        empty string if none of these are available.

        Args:
            instance: Object representing an instance, which provides context data.

        Returns:
            str: The path to the scene file as a string. Returns an empty string if no
            path is found.
        """
        # Prefer HOUDINI HIP path, then context current file, then env
        try:
            import hou  # noqa: WPS433
            return hou.hipFile.path()
        except Exception:
            return (
                instance.data.get("ifdFile")
                or ""
            )

    def _normalize_layer_name(self, name):
        """
        Normalizes a layer name.

        This method ensures compatibility with Maya naming conventions by stripping
        'rs_' prefix if present. If the input is not a string, it will be cast to a
        string.

        Parameters:
            name (str): The name of the layer to normalize.

        Returns:
            str: The normalized layer name.
        """
        # Keep Maya behavior (strip 'rs_' if present), but don’t assume it exists
        if not isinstance(name, str):
            return str(name)
        if name.startswith("rs_"):
            try:
                # Py3.9+ safe
                return name.removeprefix("rs_")
            except AttributeError:
                return name[3:]
        return name

    def process(self, instance):
        """
        Processes the given instance to prepare and create a Houdini Royal Render job.

        Summary:
        This method performs several operations to set up a render job for Houdini in
        the Royal Render system. It determines the scene path, validates the instance,
        prepares common data, ensures proper resolution, computes the layer name to appear
        in the Royal Render UI, creates the render job, and registers the job in the instance
        for downstream processing.

        Args:
            instance: The instance to process, containing all necessary data for
                      setting up the render job.

        Raises:
            Some specific errors may be raised during processing if the instance
            data is invalid, the resolution is missing, or other critical information
            for creating the render job is unavailable.
        """
        # Figure out scene path early (BaseCreateRoyalRenderJob expects it)
        self.scene_path = self._get_scene_path(instance)

        # Run base logic (validations, common data prep, etc.)
        super(CreateHoudiniRoyalRenderJob, self).process(instance)

        # Determine first expected output file to fill outputDir and job
        expected_files = instance.data["expectedFiles"]
        first_file_path = next(iter_expected_files(expected_files))
        output_dir = os.path.dirname(first_file_path)
        instance.data["outputDir"] = output_dir

        self._ensure_resolution(instance)

        # Reasonable “layer name” for RR UI: prefer product/subset/label, fall back
        layer_name = (
            instance.data.get("productName")
            or instance.data.get("subset")
            or instance.data.get("label")
            or instance.data.get("setMembers")  # some collectors use this
            or os.path.splitext(os.path.basename(self.scene_path))[0]
        )
        layer_name = f"/out/{self._normalize_layer_name(layer_name)}"

        # Build RR job using the common helper from the base class
        job = self.get_job(instance, self.scene_path, first_file_path, layer_name)
        job = self.update_job_with_host_specific(instance, job)
        # self.log.info(f"Created job: {job}")

        # Register the job so downstream integrators can depend on it
        instance.data.setdefault("rrJobs", []).append(job)

        meta_dir = self._get_metadata_dir(job)
        self.log.info(f"meta_dir::{meta_dir}")
        context = self._get_context(job)
        self.log.info(f"context::{context}")
        executable = self._get_executable()
        self.log.info(f"executable::{executable}")
        extracted_env = self._extract_environments(executable, context, job)
        self.log.info(f"extracted_env::{extracted_env}")
        rrEnv_path = self._create_rrEnv(meta_dir, extracted_env)
        self.log.info(f"Ayon job environment exported to rrEnv file:\n{rrEnv_path}")

    def _get_metadata_dir(self, job):
        """Get folder where metadata.json and renders should be produced."""
        sys.path.append(os.environ["RR_ROOT"] + "/render_apps/scripts")
        self.log.info(f"JOB: {job}")

        new_path = job.ImageDir

        self.log.info(f"_get_metadata_dir::{new_path}")
        return new_path

    def _get_context(self, job):
        envs = self._get_job_environments(job)
        return {
            "project": envs["AYON_PROJECT_NAME"],
            "asset": envs["AYON_FOLDER_PATH"],
            "task": envs["AYON_TASK_NAME"],
            "app": envs["AYON_APP_NAME"],
            "envgroup": "farm",
        }

    def _get_executable(self):
        # rr_python_utils.cache.get_rr_bin_folder()  # TODO maybe useful
        return os.environ["AYON_EXECUTABLE"]

    def _get_launch_environments(self, job):
        """Enhances environemnt with required for Ayon to be launched."""
        job_envs = self._get_job_environments(job)
        ayon_environment = {
            "AYON_SERVER_URL": os.environ["AYON_SERVER_URL"],
            "AYON_API_KEY": os.environ["AYON_API_KEY"],
            "AYON_STUDIO_BUNDLE_NAME": job_envs["AYON_STUDIO_BUNDLE_NAME"],
            "AYON_BUNDLE_NAME": job_envs["AYON_BUNDLE_NAME"],
        }
        self.log.info("Ayon launch environments:: {}".format(ayon_environment))
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

    def _extract_environments(self, executable, context, job):
        # tempfile.TemporaryFile cannot be used because of locking
        export_url = self._get_export_url()

        args = [executable, "--headless", "addon", "applications", "extractenvironments", export_url]

        if all(context.values()):
            for key, value in context.items():
                if key == "asset":
                    key = "folder"
                args.extend(["--{}".format(key), value])

        environments = self._get_launch_environments(job)

        self.log.info("Running:: {}".format(args))
        proc = subprocess.Popen(
            args,
            env=environments,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        output, error = proc.communicate()

        if not os.path.exists(export_url):
            self.log.info("output::{}".format(output))
            self.log.error("error::{}".format(error))
            raise RuntimeError("Extract failed with {}".format(error))

        with open(export_url) as json_file:
            return json.load(json_file)

    def _get_job_environments(self, job):
        """Gets environments set on job.

        It seems that it is not possible to query "rrEnvList" on job directly,
        it must be parsed from .json document.
        """
        # job = self._get_job()
        env_list = job.rrEnvList
        envs = {}
        for env in env_list.split("~~~"):
            key, value = env.split("=")
            envs[key] = value

        return envs

    def _create_rrEnv(self, meta_dir, extracted_env):
        """Create rrEnv.rrEnv file in metadata folder that render job points"""
        filter_out = os.environ.get("AYON_FILTER_ENVIRONMENTS")
        # ToDo: Improve on this
        filter_in = [
            "AYON_EXECUTABLE",
            "AYON_SERVER_URL",
            "AYON_API_KEY",
            "AYON_USE_STAGING",
            "AYON_USE_DEV",
        ]

        filter_envs = set()
        if filter_out:
            filter_envs = set(filter_out.split(";"))

        lines = []
        for key, value in extracted_env.items():
            if key in filter_envs:
                continue

            if key in filter_in:
                line = f"{key} = {value}"
                lines.append(line)

        rrenv_path = os.path.join(meta_dir, "rrEnv.rrEnv")

        if not os.path.exists(meta_dir):
            os.makedirs(meta_dir)

        with open(rrenv_path, "w") as fp:
            fp.writelines(s + "\n" for s in lines)

        return os.path.normpath(rrenv_path)

    def get_rop(self, instance):
        try:
            import hou

            # Get the ROP node from instance
            rop_path = instance.data.get("instance_node")
            if not rop_path:
                raise RuntimeError("No instance_node found in instance data")

            rop = hou.node(rop_path)
            return rop
        except Exception as exc:
            raise RuntimeError(f"Failed to get rop: {exc!r}")

    def get_renderer(self, rop):
        try:
            import hou
            if rop:
                renderer_parm = rop.parm("renderer")
                if renderer_parm:
                    return renderer_parm.evalAsString()
            raise RuntimeError(f"Failed to get renderer: {rop}")

        except Exception as exc:
            raise RuntimeError(f"Failed to get renderer: {exc!r}")

    def get_rendersettings(self, rop):
        """
        Retrieves the LOP path to the render settings from the instance node.

        This method accesses the instance node in Houdini and attempts to find
        the render settings path from the LOP network. It looks for the render
        settings primitive path that is configured on the USD Render ROP node.

        Args:
            rop: The ROP node.

        Returns:
            str: The LOP path to the render settings, or an empty string if not found.

        Raises:
            Exception: If the Houdini API is unavailable or the node cannot be accessed.
        """
        try:
            # import hou
            if not rop:
                self.log.warning(f"Could not find node at path: {rop.path()}")
                return ""

            rendersettings_parm = rop.parm("rendersettings")
            if rendersettings_parm:
                rendersettings_path = rendersettings_parm.eval()
                self.log.info(f"Found render settings path: {rendersettings_path}")
                return rendersettings_path

            raise RuntimeError(f"No render settings parameter found on node: {rop.path()}")

        except Exception as exc:
            raise RuntimeError(f"Failed to get render settings path: {exc!r}")

    def get_camera(self, instance, render_settings):
        try:
            import hou

            rop_path = instance.data.get("instance_node")
            if not rop_path:
                self.log.warning("No instance_node found in instance data")
                return ""

            rop = hou.node(rop_path)
            if not rop:
                self.log.warning(f"Could not find node at path: {rop_path}")
                return ""

            # "loppath" is a parm holding the LOP node path (string)
            loppath_parm = rop.parm("loppath")
            if not loppath_parm:
                self.log.warning(f'Node "{rop.path()}" has no "loppath" parm')
                return ""

            lop_node_path = loppath_parm.eval()
            if not lop_node_path:
                self.log.warning(f'"{rop.path()}.loppath" is empty')
                return ""

            lop_node = hou.node(lop_node_path)
            if not lop_node:
                self.log.warning(f"Could not find LOP node at path: {lop_node_path}")
                return ""

            stage = lop_node.stage()
            if not stage:
                self.log.warning(f"No stage available on LOP node: {lop_node.path()}")
                return ""

            rs_prim = stage.GetPrimAtPath(render_settings)
            if not rs_prim or not rs_prim.IsValid():
                self.log.warning(f"Could not find RenderSettings prim at: {render_settings}")
                return ""

            # RenderSettings.camera is usually a RELATIONSHIP
            cam_rel = rs_prim.GetRelationship("camera")
            if cam_rel:
                targets = cam_rel.GetTargets()
                if targets:
                    return str(targets[0])  # e.g. "/cameras/cam1"

            # Fallback if authored as an attribute in some pipelines
            cam_attr = rs_prim.GetAttribute("camera")
            if cam_attr:
                cam_val = cam_attr.Get()
                return str(cam_val) if cam_val is not None else ""

            return ""

        except Exception as exc:
            self.log.error(f"Failed to get camera: {exc!r}")
            return ""

    def _ensure_resolution(self, instance):
        """
        Ensures the resolution dimensions (width and height) are resolved and assigned to the instance
        data dictionary. The method first attempts to retrieve the resolution from the instance's data,
        then from the context's data, and finally from the render driver node using Houdini's API as a
        fallback. If no resolution can be determined, it defaults to 1920x1080.

        Args:
            instance : Any
            The instance object containing data and context used to retrieve resolution dimensions.

        Returns:
            tuple: A tuple in the form (int, int) where the first value represents the resolution
            width and the second represents the resolution height.

        Raises:
            Exception: Raised during attempts to fetch resolution from Houdini nodes when
            necessary modules or nodes fail.
        """
        ctx = instance.context
        # self.log.info(f"CTX: {ctx}")
        # self.log.info(f"INSTANCE: {instance}")
        # self.log.info(f"INSTANCE DATA: {instance.data}")


        w = instance.data.get('taskEntity').get('attrib').get('resolutionWidth')
        h = instance.data.get('taskEntity').get('attrib').get('resolutionHeight')
        if w and h:
            self.log.info(f"Found resolutionWidth & resolutionHeight {w}x{h} in instance data")
            instance.data['resolutionWidth'] = w
            instance.data['resolutionHeight'] = h
            return int(w), int(h)

        self.log.warning("No resolutionWidth and/or resolutionHeight found in instance data; "
                         "Trying to get resolution from node")
        try:
            import hou
            # Try to find the render driver node from instance data
            # Common keys you may have in your collector (adjust if yours differ):
            rop_path = instance.data.get("instance_node")
            print(f"rop path: {rop_path}")
            rop = hou.node(rop_path) if rop_path else None
            self.log.info(f"Found render driver node {rop_path}")
            self.log.info(f"Render driver node type: {rop}")

            if rop:
                # Karma (USD Render ROP / Karma LOP):
                #   - Often uses "resoverride" toggle with "p_resx"/"p_resy" ints
                # Mantra:
                #   - vm_resoverride toggle with vm_resx/vm_resy
                # Generic fallback: look for p_resx/p_resy or vm_resx/vm_resy
                # r = rop.parm

                # Karma-style
                res_override = rop.parm("resoverride")
                p_resx = rop.evalParm("resolutionx")
                p_resy = rop.evalParm("resolutiony")

                self.log.info(f"Resolution override: {res_override}")
                self.log.info(f"Resolution X: {p_resx}")
                self.log.info(f"Resolution Y: {p_resy}")

                vm_resoverride = rop.parm("vm_resoverride")
                vm_resx = rop.parm("vm_resx")
                vm_resy = rop.parm("vm_resy")

                if res_override and res_override.eval() and p_resx and p_resy:
                    w = int(p_resx.eval())
                    h = int(p_resy.eval())
                    self.log.info(f"Found resolution {w}x{h} from resoverride/p_resx/p_resy")
                elif vm_resoverride and vm_resoverride.eval() and vm_resx and vm_resy:
                    w = int(vm_resx.eval())
                    h = int(vm_resy.eval())
                    self.log.info(f"Found resolution {w}x{h} from vm_resx/vm_resy")
                else:
                    # try common parameter names if override toggles aren’t present
                    for cand_w, cand_h in (("p_resx", "p_resy"), ("vm_resx", "vm_resy"),
                                           ("resx", "resy")):
                        pw = rop.parm(cand_w)
                        ph = rop.parm(cand_h)
                        if pw and ph:
                            w = int(pw.eval())
                            h = int(ph.eval())
                            self.log.info(f"Last resort found resolution {w}x{h}")
                            break
        except Exception as exc:
            self.log.debug(f"Resolution probe via hou failed: {exc!r}")

        if not (w and h):
            self.log.warning(
                "No resolution found on instance/context/ROP; defaulting to 1920x1080.")
            w, h = 1920, 1080

        instance.data["resolutionWidth"] = int(w)
        instance.data["resolutionHeight"] = int(h)
        return int(w), int(h)

