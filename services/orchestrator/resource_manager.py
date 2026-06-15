import asyncio
import docker
import logging
import time
import hashlib
from typing import Dict, Any, List, Optional
from pydantic import BaseModel
from foundry_client import push_to_foundry

logger = logging.getLogger("vox-resource-manager")

class Task(BaseModel):
    id: str
    type: str  # "image-gen", "music-gen", "vision-scan", "sfx-gen"
    payload: Dict[str, Any]
    status: str = "queued"  # "queued", "swapping", "processing", "complete"
    progress: float = 0.0
    created_at: float

class ResourceManager:
    def __init__(self):
        # We no longer need to connect to docker for swapping, everything is resident
        self.client = None
        logger.info("✅ Resource Manager initialized in 'Always-On' mode. Swapping disabled.")

        self.queue: asyncio.Queue = asyncio.Queue()
        self.active_tasks: Dict[str, Task] = {}
        self.dedupe_cache: Dict[str, float] = {}
        self.lock = asyncio.Lock()

        # Traffic light to prevent heavy GPU tasks from running at the exact same millisecond
        self.gpu_compute_lock = asyncio.Lock()

        # State tracking (obsolete but kept for compatibility)
        self.current_resident = "always-on"
        self.burst_services = []

        # Start background worker
        self.worker_task = None
        self.processing_tasks: Dict[str, asyncio.Task] = {}

    def start_worker(self):
        if not self.worker_task:
            self.worker_task = asyncio.create_task(self._background_worker())
            logger.info("🚀 Resource Manager background worker started.")

    async def enqueue_task(self, task_type: str, payload: Dict[str, Any]):
        """Deduplicates and enqueues a new task."""
        # 1. Deduplication (15s window)
        payload_hash = hashlib.md5(str(payload).encode()).hexdigest()
        dedupe_key = f"{task_type}:{payload_hash}"
        
        now = time.time()
        if dedupe_key in self.dedupe_cache:
            if now - self.dedupe_cache[dedupe_key] < 15:
                logger.info(f"🚫 Deduplicator: Ignoring redundant {task_type} trigger.")
                return None
        
        self.dedupe_cache[dedupe_key] = now
        
        # 2. Create Task
        task_id = f"{task_type}-{int(now * 1000)}"
        task = Task(id=task_id, type=task_type, payload=payload, created_at=now)
        
        async with self.lock:
            self.active_tasks[task_id] = task
        
        await self.queue.put(task_id)
        logger.info(f"📥 Enqueued task: {task_id}")
        return task_id

    async def get_queue_status(self) -> List[Dict[str, Any]]:
        """Returns the status of all active tasks for the Foundry UI."""
        async with self.lock:
            # Clean up old complete tasks (older than 5s)
            now = time.time()
            self.active_tasks = {
                tid: t for tid, t in self.active_tasks.items() 
                if t.status != "complete" or (now - t.created_at < 5)
            }
            return [t.model_dump() for t in self.active_tasks.values()]

    async def cancel_task(self, task_id: str):
        """Cancels a task if it is in the queue or being processed."""
        async with self.lock:
            # 1. Remove from active tracking
            task = self.active_tasks.get(task_id)
            if not task:
                return False
            
            task.status = "cancelled"
            
            # 2. Kill the processing coroutine if active
            proc_task = self.processing_tasks.get(task_id)
            if proc_task:
                proc_task.cancel()
                logger.info(f"🛑 Cancelled processing for task: {task_id}")
            
            return True

    async def _background_worker(self):
        """Monitors the queue and manages the hot-swaps."""
        while True:
            task_id = await self.queue.get()
            task = self.active_tasks.get(task_id)
            if not task or task.status == "cancelled":
                self.queue.task_done()
                continue

            try:
                # Wrap process_task in a cancellable task
                p_task = asyncio.create_task(self._process_task(task))
                self.processing_tasks[task_id] = p_task
                await p_task
            except asyncio.CancelledError:
                logger.warning(f"⚠️ Task {task_id} was cancelled during execution.")
                task.status = "cancelled"
            except Exception as e:
                logger.error(f"❌ Error processing task {task_id}: {e}")
                task.status = "failed"
            finally:
                self.processing_tasks.pop(task_id, None)
                self.queue.task_done()

    async def _process_task(self, task: Task):
        """Logic for executing a task with compute serialization."""
        logger.info(f"⚙️ Processing task {task.id} ({task.type})")
        
        # 1. SFX generation is resident, no swap needed and doesn't need strict GPU lock
        if task.type == "sfx-gen":
            await self._execute_sfx_gen(task)
            return

        # 2. Execute heavy payload sequentially via lock
        task.status = "processing"
        task.progress = 0.3
        
        try:
            async with self.gpu_compute_lock:
                if task.type == "image-gen":
                    await self._execute_image_gen(task)
                elif task.type == "music-gen":
                    await self._execute_music_gen(task)
                elif task.type == "vision-scan":
                    await self._execute_vision_scan(task)
        except Exception as e:
            logger.error(f"❌ Task {task.id} execution failed: {e}")
            task.status = "failed"
            return
        
        task.status = "complete"
        task.progress = 1.0
        logger.info(f"✅ Task {task.id} complete.")

    async def _execute_sfx_gen(self, task: Task):
        """Calls the resident SFX generator with Vault Cache and Pricing Multiplier."""
        import httpx
        import hashlib
        from ledger import ledger, VAULT_DIR
        
        prompt = task.payload.get("prompt", "")
        sound_id = hashlib.md5(prompt.encode()).hexdigest()
        vault_path = VAULT_DIR / f"{sound_id}.wav"
        
        user_id = task.payload.get("userId", "gm") # Default to gm if autonomous
        tier = "optimal" # Assuming resident rig is optimal
        
        is_replay = vault_path.exists()
        cost = ledger.calculate_cost("audio", tier, is_replay=is_replay)
        
        try:
            ledger.charge(user_id, cost, f"SFX {'Replay' if is_replay else 'Generation'}: {prompt[:30]}")
        except ValueError as e:
            logger.warning(f"⚠️ Insufficient funds for SFX: {e}")
            task.status = "failed"
            return

        if is_replay:
            logger.info(f"📂 Vault Hit: Streaming {sound_id}.wav for {task.id}")
            task.progress = 1.0
            task.status = "complete"
            # Logic to stream file back to Foundry would go here
            await push_to_foundry("sfx-trigger", {"sound_id": sound_id, "prompt": prompt})
            return

        url = "http://vox-audio-generation-sfx:8000/generate"
        task.status = "processing"
        task.progress = 0.2
        async with httpx.AsyncClient(timeout=120.0) as client:
            resp = await client.post(url, json=task.payload)
            if resp.status_code == 200:
                # Store in vault for future replays
                with open(vault_path, "wb") as f:
                    f.write(resp.content)
                logger.info(f"💾 SFX saved to vault: {sound_id}.wav")
                await push_to_foundry("sfx-trigger", {"sound_id": sound_id, "prompt": prompt})
                task.progress = 1.0
            else:
                ledger.refund(user_id, cost, "SFX Generation Failed")
                raise RuntimeError(f"SFX service failed: {resp.status_code}")

    async def _execute_image_gen(self, task: Task):
        """Calls the hot-swapped SDXL generator and pushes result to Foundry."""
        import httpx
        from ledger import ledger
        
        user_id = task.payload.get("userId", "gm")
        tier = "optimal" # Assuming resident rig is optimal
        cost = ledger.calculate_cost("image", tier)
        
        try:
            ledger.charge(user_id, cost, f"Image Gen: {task.payload.get('prompt', '')[:30]}")
        except ValueError as e:
            logger.warning(f"⚠️ Insufficient funds for Image Gen: {e}")
            task.status = "failed"
            return

        url = "http://vox-vision-gen:8003/generate"
        task.progress = 0.4
        async with httpx.AsyncClient(timeout=180.0) as client:
            resp = await client.post(url, json=task.payload)
            task.progress = 0.9
            if resp.status_code == 200:
                # Dispatch back to Foundry
                success = await push_to_foundry(
                    update_type=task.payload.get("target", "atmosphere"),
                    content=resp.content,
                    original_prompt=task.payload.get("original_prompt", "")
                )
                if not success:
                    logger.warning(f"⚠️ Failed to push generated image to Foundry for {task.id}")
            else:
                ledger.refund(user_id, cost, "Image Gen Failed")
                raise RuntimeError(f"Image Gen failed: {resp.status_code}")

    async def _execute_vision_scan(self, task: Task):
        """Calls the hot-swapped MiniCPM-V reader and processes battlemap analysis."""
        import httpx
        import base64
        import json
        from pathlib import Path
        
        image_path = Path("/foundry_data") / task.payload["image_path"]
        if not image_path.exists():
            # Try without leading slash
            image_path = Path("/foundry_data") / task.payload["image_path"].lstrip("/")
            
        if not image_path.exists():
            raise FileNotFoundError(f"Image not found at {image_path}")
            
        task.progress = 0.2
        with open(image_path, "rb") as f:
            img_b64 = base64.b64encode(f.read()).decode('utf-8')
        data_uri = f"data:image/png;base64,{img_b64}"

        # ... (rest of vision scan logic already implemented) ...
        # I'll just restore the rest of the method correctly this time.
        
        vision_payload = {
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": (
                                "Analyze this tabletop battlemap image. "
                                "Identify walls (including any door walls), lights, and sound-emitting objects.\n\n"
                                "Respond ONLY with a valid JSON object. Use EXACTLY these keys:\n"
                                "{\n"
                                '  "image": {"width": 1000},\n'
                                '  "walls": [{"c": [x0, y0, x1, y1], "door": 0|1}],\n'
                                '  "lights": [{"x": fx, "y": fy, "dim": 1-10, "bright": 1-5, "color": "#hex"}],\n'
                                '  "sound_sources": [{"x": fx, "y": fy, "radius_units": 1-20, "sfx_description": "<vivid description>", "duration_seconds": 2-10}]\n'
                                "}"
                            ),
                        },
                        {"type": "image_url", "image_url": {"url": data_uri}},
                    ],
                }
            ],
            "temperature": 0.1,
        }

        url = "http://vox-vision-reader:8000/v1/chat/completions"
        async with httpx.AsyncClient(timeout=300.0) as client:
            resp = await client.post(url, json=vision_payload)
            if resp.status_code != 200:
                raise RuntimeError(f"Vision reader returned {resp.status_code}")

            raw_text = resp.json()["choices"][0]["message"]["content"]
            try:
                import re
                match = re.search(r"\{.*\}", raw_text, re.DOTALL)
                if match:
                    contract = json.loads(match.group())
                    contract["sceneId"] = task.payload["scene_id"]
                    
                    logger.info(f"✅ Vision Scan contract extracted for {task.id}. Pushing to Foundry...")
                    await push_to_foundry("vision-contract", contract)
                    
                    for src in contract.get("sound_sources", []):
                        await self.enqueue_task("sfx-gen", {
                            "prompt": src["sfx_description"],
                            "duration_seconds": src["duration_seconds"],
                            "userId": task.payload.get("userId", "gm")
                        })
                else:
                    logger.warning(f"⚠️ No JSON found in vision response for {task.id}")
            except Exception as e:
                logger.error(f"Failed to parse vision response: {e}")

        task.progress = 1.0

    async def _execute_music_gen(self, task: Task):
        """Calls the hot-swapped Music generator."""
        import httpx
        from ledger import ledger
        
        # Ambient sounds and music belong to the DM account
        user_id = "gm" 
        tier = "optimal"
        cost = ledger.calculate_cost("audio", tier)
        
        try:
            ledger.charge(user_id, cost, f"Atmospheric Music: {task.payload.get('prompt', '')[:30]}")
        except ValueError as e:
            logger.warning(f"⚠️ Insufficient funds for Music Gen: {e}")
            task.status = "failed"
            return

        url = "http://vox-audio-generation-music:8000/generate"
        task.progress = 0.4
        async with httpx.AsyncClient(timeout=300.0) as client:
            resp = await client.post(url, json=task.payload)
            if resp.status_code == 200:
                task.progress = 1.0
            else:
                ledger.refund(user_id, cost, "Music Gen Failed")
                raise RuntimeError(f"Music Gen failed: {resp.status_code}")

# Singleton instance
resource_manager = ResourceManager()
# resource_manager.start_worker() # Must be called inside a running event loop (e.g. app startup)
