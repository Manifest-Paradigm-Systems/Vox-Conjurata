import logging
import asyncio

logger = logging.getLogger("vox-conjurata-containers")

class ContainerManager:
    def __init__(self):
        logger.info("ContainerManager initialized in 'Always-On' mode. Swapping disabled.")

    async def start_container(self, name: str):
        pass

    async def stop_container(self, name: str):
        pass

    async def swap_to_warm_scene_load(self):
        """Always-On Architecture: All containers remain resident."""
        logger.info("[Always-On] Bypassing WARM swap. All containers resident.")

    async def swap_to_hot_combat(self):
        """Always-On Architecture: All containers remain resident."""
        logger.info("[Always-On] Bypassing HOT swap. All containers resident.")

    async def start_music_gen(self):
        pass
        
    async def stop_music_gen(self):
        pass

container_manager = ContainerManager()
