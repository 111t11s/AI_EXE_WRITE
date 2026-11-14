"""Keep the browser's frontend modules in sync with server updates."""

from fastapi.staticfiles import StaticFiles


class FrontendFiles(StaticFiles):
    async def get_response(self, path, scope):
        response = await super().get_response(path, scope)
        # Revalidate HTML and its module dependencies, including cached 304s.
        response.headers["Cache-Control"] = "no-cache"
        return response
