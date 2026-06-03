# dm_approval.py — DM approval system for unauthorized users
#
# When a non-allowlisted user DMs Bong, this module sends a request to Eve
# with tier selection buttons. Approved users are persisted via user_data.py
# into users.json.

import discord
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import debug
import persist
import user_data

_APPROVAL_STORE_PATH = user_data.BONG_USER_DATA / "pending_approvals.json"
_approval_store = persist.PersistStore(_APPROVAL_STORE_PATH, default=[])
persist.register(_approval_store)

pending_approval: set[int] = set()


def load_pending_approvals():
    _approval_store.load()
    pending_approval.clear()
    pending_approval.update(_approval_store.data)


def _sync_store():
    _approval_store.data = list(pending_approval)
    _approval_store.mark_dirty()

# The owner who receives approval requests
OWNER_ID = user_data.OWNER_ID


class ApproveView(discord.ui.View):
    """Discord UI view with tier selection buttons for DM access requests."""

    def __init__(self, requesting_user: discord.User | discord.Member):
        super().__init__(timeout=300)
        self.requesting_user = requesting_user
        self._expired = False

    async def _approve(self, interaction: discord.Interaction, tier: str):
        if interaction.user.id != OWNER_ID:
            await interaction.response.send_message("Only Eve can approve DM access.", ephemeral=True)
            return
        if self._expired:
            await interaction.response.edit_message(content="⏰ This request has already timed out.", view=None)
            return
        tier_label = {"admin": "Admin", "authorized": "Authorized", "user": "User"}.get(tier, tier.title())
        await interaction.response.edit_message(
            content=f"✅ Approved **{self.requesting_user.display_name}** ({self.requesting_user.id}) as **{tier_label}**.",
            view=None,
        )
        user_data.set_tier(self.requesting_user.id, tier)
        pending_approval.discard(self.requesting_user.id)
        _sync_store()
        self.stop()
        try:
            await self.requesting_user.send("Eve has approved you to talk with me! You can now send me messages here. 🎉")
        except discord.Forbidden:
            pass

    @discord.ui.button(label="User", style=discord.ButtonStyle.secondary, emoji="👥")
    async def approve_user(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._approve(interaction, "user")

    @discord.ui.button(label="Authorized", style=discord.ButtonStyle.primary, emoji="🔐")
    async def approve_authorized(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._approve(interaction, "authorized")

    @discord.ui.button(label="Admin", style=discord.ButtonStyle.success, emoji="👑")
    async def approve_admin(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._approve(interaction, "admin")

    @discord.ui.button(label="Deny", style=discord.ButtonStyle.danger, emoji="❌")
    async def deny(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != OWNER_ID:
            await interaction.response.send_message("Only Eve can deny DM access.", ephemeral=True)
            return
        if self._expired:
            await interaction.response.edit_message(content="⏰ This request has already timed out.", view=None)
            return
        await interaction.response.edit_message(
            content=f"❌ Denied **{self.requesting_user.display_name}** ({self.requesting_user.id}) DM access.",
            view=None,
        )
        pending_approval.discard(self.requesting_user.id)
        _sync_store()
        self.stop()
        try:
            await self.requesting_user.send("Eve has denied your request to talk with me. Sorry!")
        except discord.Forbidden:
            pass

    async def on_timeout(self):
        self._expired = True
        pending_approval.discard(self.requesting_user.id)
        _sync_store()
        try:
            await self.requesting_user.send("Your approval request has timed out.")
        except Exception:
            pass


async def process_dm(message: discord.Message, bot: discord.Client) -> bool:
    """Process a DM message. Returns True if the message should be handled by Bong.

    If the user is not in any tier, sends an approval request to Eve and returns False.
    If the user is in any tier, returns True.
    If the user has a pending request, tells them to wait and returns False.
    """
    user = message.author

    if user_data.is_known(user.id):
        return True

    if user.id in pending_approval:
        try:
            await message.channel.send("Your request is still pending — Eve hasn't responded yet!")
        except discord.Forbidden:
            pass
        return False

    pending_approval.add(user.id)
    _sync_store()

    owner = bot.get_user(OWNER_ID)
    if not owner:
        try:
            owner = await bot.fetch_user(OWNER_ID)
        except Exception as e:
            debug.error("Approval", f"Failed to fetch owner: {e}")
            pending_approval.discard(user.id)
            _sync_store()
            return False

    preview = (message.content[:100] + "...") if len(message.content) > 100 else (message.content or ("(attachment)" if message.attachments else "(empty message)"))
    view = ApproveView(user)
    try:
        await owner.send(
            f"🔒 **DM Access Request**\n"
            f"**{user.display_name}** (`{user.id}`) wants to talk to Bong in DMs.\n"
            f"First message: \"{preview}\"",
            view=view,
        )
    except discord.Forbidden:
        pending_approval.discard(user.id)
        _sync_store()
        return False

    try:
        await user.send("I've sent your request to Eve. I'll let you know once she decides! 📬")
    except discord.Forbidden:
        pass

    return False