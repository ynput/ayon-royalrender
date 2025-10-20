# RoyalRender addon
RoyalRender integration for AYON.

### Installation

Files from `ayon-royalrender/client/ayon_royalrender/rr_root/render_apps` needs to be copied to `RR_ROOT`.

Each render node needs to have AYON Launcher executable installed. Default location is set in `rrConfig.AYON`.

### Expected environment variables set on Render nodes

Render jobs needs to get environment variables set and controlled by AYON for particular render node.
For this reason `injection` of environment variables process is triggered for each of the nodes. Process
runs `ayon_console --headless extractenvironments PATH_TO_EXTRACTED_FILE`.

For this it needs to now location of `ayon_console` which should be get from config of AYON rrApp, but currently
it is not technically possible.

So each render node need to have these environment variables set:
- AYON_SERVER_URL (http://localhost:5000)
- AYON_API_KEY (api key of newly created service account in AYON)
- AYON_EXECUTABLE (path to ayon_console on this particular machine)