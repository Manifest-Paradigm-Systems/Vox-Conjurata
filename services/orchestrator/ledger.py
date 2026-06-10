import json
import time
import logging
import hashlib
from pathlib import Path
from typing import Dict, Optional, List
from pydantic import BaseModel

logger = logging.getLogger("vox-ledger")

VAULT_DIR = Path("./vault")
VAULT_DIR.mkdir(parents=True, exist_ok=True)

LEDGER_PATH = Path("./settings/campaign_ledger.json")

class PricingTier(BaseModel):
    base: float
    fee: float

class PriceMatrix:
    # Target Subsystem: (Optimal Base + Fee, Budget Base + Fee)
    MATRIX = {
        "llm": {"optimal": PricingTier(base=0.0000, fee=0.0030), "budget": PricingTier(base=0.0005, fee=0.0020)},
        "tts": {"optimal": PricingTier(base=0.0000, fee=0.0030), "budget": PricingTier(base=0.0015, fee=0.0020)},
        "image": {"optimal": PricingTier(base=0.0000, fee=0.0030), "budget": PricingTier(base=0.0030, fee=0.0020)},
        "audio": {"optimal": PricingTier(base=0.0500, fee=0.0030), "budget": PricingTier(base=0.0500, fee=0.0020)},
        "sfx_replay": {"optimal": PricingTier(base=0.0000, fee=0.0030), "budget": PricingTier(base=0.0000, fee=0.0020)},
        "vision": {"optimal": PricingTier(base=0.0000, fee=0.0030), "budget": PricingTier(base=0.0050, fee=0.0020)},
    }

    CACHE_SURCHARGE = 0.0005 # per 1K tokens (simulated)
    CACHE_DISCOUNT_MULTIPLIER = 0.5

class LedgerState(BaseModel):
    # Campaign-level data
    campaign_pool: float = 0.0
    stripe_customer_id: Optional[str] = None
    
    # Persistent Player Wallets (Carry over across sessions)
    personal_wallets: Dict[str, float] = {} # userId -> persistent_balance
    
    # Session-level data (DM-granted temporary funds)
    session_allowances: Dict[str, float] = {} # userId -> current_session_grant
    session_caps: Dict[str, float] = {} # userId -> session_limit (max cap assigned)
    session_spent: Dict[str, float] = {} # userId -> total_spent_this_session
    
    transactions: List[dict] = []

class VoxLedger:
    def __init__(self):
        self.state = self._load_ledger()
        self.warm_cache_fingerprints: set[str] = set()
        self.LOW_BALANCE_THRESHOLD = 0.20

    def _load_ledger(self) -> LedgerState:
        if LEDGER_PATH.exists():
            try:
                with open(LEDGER_PATH, "r") as f:
                    return LedgerState.model_validate_json(f.read())
            except Exception as e:
                logger.error(f"Failed to load ledger: {e}")
        return LedgerState()

    def _save_ledger(self):
        LEDGER_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(LEDGER_PATH, "w") as f:
            f.write(self.state.model_dump_json(indent=2))

    def top_up_pool(self, amount: float):
        self.state.campaign_pool += amount
        self._save_ledger()
        logger.info(f"💰 Campaign Pool topped up by ${amount}. Total: ${self.state.campaign_pool}")

    def add_to_personal_wallet(self, user_id: str, amount: float):
        """Adds credits directly to a player's persistent wallet (usually via purchase)."""
        current = self.state.personal_wallets.get(user_id, 0.0)
        self.state.personal_wallets[user_id] = current + amount
        self._save_ledger()
        logger.info(f"💼 Persistent Wallet: Added ${amount} to {user_id}. Total: ${self.state.personal_wallets[user_id]}")

    def set_session_allowance(self, user_id: str, amount: float):
        """DM allocates session-specific funds from the campaign pool."""
        current_grant = self.state.session_allowances.get(user_id, 0.0)
        needed = amount - current_grant
        
        if needed > 0:
            if self.state.campaign_pool < needed:
                raise ValueError("Insufficient Campaign Pool funds for this session grant.")
            self.state.campaign_pool -= needed
        else:
            self.state.campaign_pool += abs(needed)
            
        self.state.session_allowances[user_id] = amount
        self.state.session_caps[user_id] = amount
        self.state.session_spent[user_id] = 0.0 
        self._save_ledger()

    def return_to_pool(self, user_id: str, from_personal: bool = False):
        """Allows a player to return unused session grants or personal credits to the pool."""
        if from_personal:
            amount = self.state.personal_wallets.get(user_id, 0.0)
            self.state.campaign_pool += amount
            self.state.personal_wallets[user_id] = 0.0
            logger.info(f"🔄 {user_id} returned persistent wallet (${amount}) to pool.")
        else:
            amount = self.state.session_allowances.get(user_id, 0.0)
            self.state.campaign_pool += amount
            self.state.session_allowances[user_id] = 0.0
            logger.info(f"🔄 {user_id} returned session grant (${amount}) to pool.")
        self._save_ledger()

    def transfer_credits(self, from_user: str, to_user: str, amount: float, from_personal: bool = True):
        """Granular transfer between users or to the Campaign Pool."""
        if amount <= 0:
            raise ValueError("Transfer amount must be positive.")

        # 1. Determine Source
        if from_personal:
            source_balance = self.state.personal_wallets.get(from_user, 0.0)
        else:
            source_balance = self.state.session_allowances.get(from_user, 0.0)

        if source_balance < amount:
            raise ValueError(f"Insufficient funds for transfer. Have ${source_balance:.2f}, need ${amount:.2f}")

        # 2. Deduct from Source
        if from_personal:
            self.state.personal_wallets[from_user] -= amount
        else:
            self.state.session_allowances[from_user] -= amount

        # 3. Add to Target
        if to_user == "POOL":
            self.state.campaign_pool += amount
            logger.info(f"🎁 {from_user} gifted ${amount} to the Campaign Pool.")
        else:
            # Transfers between users always go to the recipient's Personal Wallet for permanence
            current = self.state.personal_wallets.get(to_user, 0.0)
            self.state.personal_wallets[to_user] = current + amount
            logger.info(f"🎁 {from_user} gifted ${amount} to {to_user}.")

        self.state.transactions.append({
            "user_id": from_user,
            "target_user_id": to_user,
            "amount": amount,
            "description": f"GIFT: {from_user} -> {to_user}",
            "timestamp": time.time(),
            "is_gift": True
        })
        self._save_ledger()

    def get_balance(self, user_id: str) -> dict:
        session_grant = self.state.session_allowances.get(user_id, 0.0)
        personal_wallet = self.state.personal_wallets.get(user_id, 0.0)
        total_available = session_grant + personal_wallet
        
        return {
            "campaign_pool": self.state.campaign_pool,
            "session_grant": session_grant,
            "personal_wallet": personal_wallet,
            "total_available": total_available,
            "is_out_of_credits": total_available <= 0.0,
            "is_low_balance": 0 < total_available <= self.LOW_BALANCE_THRESHOLD,
            "pool_dry": self.state.campaign_pool <= 0.0
        }

    def calculate_cost(self, subsystem: str, tier: str, is_replay: bool = False, prompt: Optional[str] = None) -> float:
        key = subsystem
        if subsystem == "audio" and is_replay:
            key = "sfx_replay"
            
        pricing = PriceMatrix.MATRIX.get(key, {}).get(tier)
        if not pricing:
            return 0.0
            
        cost = pricing.base + pricing.fee
        
        # Apply Cache Logic for LLM/Budget
        if subsystem == "llm" and tier == "budget" and prompt:
            fingerprint = hashlib.md5(prompt.encode()).hexdigest()
            if fingerprint in self.warm_cache_fingerprints:
                cost -= (PriceMatrix.CACHE_SURCHARGE * 0.5) 
            else:
                cost += PriceMatrix.CACHE_SURCHARGE
                self.warm_cache_fingerprints.add(fingerprint)
                
        return round(cost, 6)

    def charge(self, user_id: str, amount: float, description: str):
        if user_id == "ADMIN":
            logger.info(f"⚡ ADMIN Action: {description} (Bypassing charge of ${amount})")
            return

        session_grant = self.state.session_allowances.get(user_id, 0.0)
        personal_wallet = self.state.personal_wallets.get(user_id, 0.0)
        
        if (session_grant + personal_wallet) < amount:
            raise ValueError(f"Insufficient funds. Needed ${amount}, have ${session_grant + personal_wallet:.4f}.")

        # Order of operations: Use Session Grant (DM funded) first, then Personal Wallet
        remaining_charge = amount
        if session_grant > 0:
            used_from_grant = min(session_grant, remaining_charge)
            self.state.session_allowances[user_id] -= used_from_grant
            remaining_charge -= used_from_grant
            
        if remaining_charge > 0:
            self.state.personal_wallets[user_id] -= remaining_charge
            
        self.state.session_spent[user_id] = self.state.session_spent.get(user_id, 0.0) + amount
        
        self.state.transactions.append({
            "user_id": user_id, "amount": amount, "description": description, "timestamp": time.time()
        })
        self._save_ledger()
        
        total_rem = self.state.session_allowances.get(user_id, 0.0) + self.state.personal_wallets.get(user_id, 0.0)
        if 0 < total_rem <= self.LOW_BALANCE_THRESHOLD:
            logger.warning(f"⚠️ LOW BALANCE ALERT for {user_id}: ${total_rem:.2f} remaining.")

    def refund(self, user_id: str, amount: float, description: str):
        if user_id == "ADMIN": return
        # Refund goes back to personal wallet by default for safety
        self.state.personal_wallets[user_id] = self.state.personal_wallets.get(user_id, 0.0) + amount
        self.state.transactions.append({
            "user_id": user_id, "amount": -amount, "description": f"REFUND: {description}", "timestamp": time.time()
        })
        self._save_ledger()

    def admin_modify_credits(self, target_user_id: str, amount: float, description: str = "Admin Adjustment"):
        """ADMIN ONLY: Forcefully add or subtract credits from any account."""
        current = self.state.personal_wallets.get(target_user_id, 0.0)
        new_total = max(0, current + amount)
        self.state.personal_wallets[target_user_id] = new_total
        
        self.state.transactions.append({
            "user_id": target_user_id,
            "amount": -amount,
            "description": f"ADMIN OVERRIDE: {description}",
            "timestamp": time.time(),
            "is_admin_action": True
        })
        self._save_ledger()
        logger.info(f"🛡️ ADMIN: Modified {target_user_id} credits by ${amount}. New: ${new_total}")

    def admin_set_pool(self, amount: float):
        """ADMIN ONLY: Forcefully set the Campaign Pool balance."""
        self.state.campaign_pool = max(0, amount)
        self._save_ledger()
        logger.info(f"🛡️ ADMIN: Set Campaign Pool to ${self.state.campaign_pool}")

ledger = VoxLedger()
