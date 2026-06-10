import docker
import logging
import asyncio

logger = logging.getLogger("vox-conjurata-containers")

class ContainerManager:
    def __init__(self):
        try:
            self.client = docker.DockerClient(base_url='unix://var/run/docker.sock')
            logger.info("Connected to Podman/Docker socket.")
        except Exception as e:
            logger.error(f"Failed to connect to socket: {e}")
            self.client = None

    def _get_container(self, name: str):
        if not self.client: return None
        try:
            return self.client.containers.get(name)
        except Exception as e:
            logger.warning(f"Container {name} not found: {e}")
            return None

    def _start_sync(self, name: str):
        c = self._get_container(name)
        if c and c.status != "running":
            logger.info(f"Starting {name}...")
            c.start()

    def _stop_sync(self, name: str):
        c = self._get_container(name)
        if c and c.status == "running":
            logger.info(f"Stopping {name}...")
            c.stop(timeout=5)

    async def start_container(self, name: str):
        await asyncio.to_thread(self._start_sync, name)
        # Give the container a few seconds to load models into VRAM
        await asyncio.sleep(5)

    async def stop_container(self, name: str):
        await asyncio.to_thread(self._stop_sync, name)

    async def swap_to_warm_scene_load(self):
        """Used during scene transitions. Unloads SDXL to free VRAM for Reader + Music."""
        logger.info("Initiating WARM swap for Scene Load...")
        await self.stop_container("vox-vision-gen")
        # Wait a moment for VRAM to clear
        await asyncio.sleep(2)
        await self.start_container("vox-vision-reader")
        await self.start_container("vox-audio-generation-music")

    async def swap_to_hot_combat(self):
        """Restores the default HOT state for combat (SDXL running, Reader/Music stopped)."""
        logger.info("Initiating HOT swap for Combat...")
        await self.stop_container("vox-vision-reader")
        await self.stop_container("vox-audio-generation-music")
        await asyncio.sleep(2)
        await self.start_container("vox-vision-gen")

    async def start_music_gen(self):
        """Starts music generation independently (fits within combat headroom)."""
        await self.start_container("vox-audio-generation-music")
        
    async def stop_music_gen(self):
        await self.stop_container("vox-audio-generation-music")

container_manager = ContainerManager()
