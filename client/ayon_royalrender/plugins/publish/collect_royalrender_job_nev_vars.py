import os

import pyblish.api

from ayon_core.pipeline.publish import FARM_JOB_ENV_DATA_KEY


class CollectRoyalRenderJobEnvVars(pyblish.api.ContextPlugin):
    """Collect set of environment variables to submit with RR jobs"""
    order = pyblish.api.CollectorOrder
    label = "RoyalRender Farm Environment Variables"
    targets = ["local"]

    ENV_KEYS = [
        # applications addon
        "AYON_APP_NAME",

        # ftrack addon
        "FTRACK_API_KEY",
        "FTRACK_API_USER",
        "FTRACK_SERVER",

        # kitsu addon
        "KITSU_SERVER",
        "KITSU_LOGIN",
        "KITSU_PWD",

        # Shotgrid / Flow addon
        "AYON_SG_USERNAME",

        # Not sure how this is usefull for farm, scared to remove
        "PYBLISHPLUGINPATH",
    ]

    def process(self, context):
        env = context.data.setdefault(FARM_JOB_ENV_DATA_KEY, {})
        for key in self.ENV_KEYS:
            # Skip already set keys
            if key in env:
                continue
            value = os.getenv(key)
            if value:
                self.log.debug(f"Setting job env: {key}: {value}")
                env[key] = value

        if os.environ.get("AYON_USE_STAGING"):
            env["AYON_USE_STAGING"] = "1"