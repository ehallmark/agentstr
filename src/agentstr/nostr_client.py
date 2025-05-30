import logging
import uuid
import time
import threading
from typing import List, Any, Optional, Callable
from pynostr.key import PrivateKey
from pynostr.event import Event, EventKind
from pynostr.filters import Filters
from pynostr.metadata import Metadata
from pynostr.utils import get_public_key, get_timestamp
from agentstr.nwc_client import NWCClient
from agentstr.nostr_event_relay import DecryptedMessage, EventRelay

logging.basicConfig(level=logging.WARNING)
logger = logging.getLogger(__name__)
ack = set([])


def log_callback(*args):
    """Default callback for logging relay messages."""
    logging.info(f"Received message from {args}")


class NostrClient:
    """A client for interacting with the Nostr protocol, handling events, direct messages, and metadata.

    This class provides methods to connect to Nostr relays, send and receive direct messages,
    manage metadata, and read posts by tags. It integrates with Nostr Wallet Connect (NWC)
    for payment processing if provided.

    Attributes:
        relays (List[str]): List of Nostr relay URLs.
        private_key (PrivateKey): The private key for signing events.
        public_key (PublicKey): The public key derived from the private key.
        nwc_client (NWCClient | None): Nostr Wallet Connect client for payment processing.
    """
    def __init__(self, relays: List[str], private_key: str = None, nwc_str: str = None):
        """Initialize the NostrClient.

        Args:
            relays: List of Nostr relay URLs to connect to.
            private_key: Nostr private key in 'nsec' format.
            nwc_str: Nostr Wallet Connect string for payment processing (optional).
        """
        self.relays = relays
        self.private_key = PrivateKey.from_nsec(private_key) if private_key else None
        self.public_key = self.private_key.public_key if self.private_key else None
        self.nwc_client = NWCClient(nwc_str) if nwc_str else None

    @property
    def messenger(self):
        return EventRelay(self.relays[0], self.private_key)

    def sign(self, event: Event) -> Event:
        """Sign an event with the client's private key.

        Args:
            event: The Nostr event to sign.

        Returns:
            The signed event.
        """
        event.sign(self.private_key.hex())
        return event

    def read_posts_by_tag(self, tag: str = None, tags: List[str] = None, limit: int = 10) -> List[Event]:
        """Read posts containing a specific tag from Nostr relays.

        Args:
            tag: The tag to filter posts by.
            tags: List of tags to filter posts by.
            limit: Maximum number of posts to retrieve.

        Returns:
            List of Events.
        """
        filters = Filters(limit=limit, kinds=[EventKind.TEXT_NOTE])
        filters.add_arbitrary_tag("t", tags or [tag])

        return self.messenger.get_events(filters)

        

    def get_metadata_for_pubkey(self, public_key: str | PrivateKey = None) -> Optional[Metadata]:
        """Retrieve metadata for a given public key.

        Args:
            public_key: The public key to fetch metadata for (defaults to client's public key).

        Returns:
            Metadata object or None if not found.
        """
        public_key = get_public_key(public_key if isinstance(public_key, str) else public_key.hex()) if public_key else self.public_key
        filters = Filters(kinds=[EventKind.SET_METADATA], authors=[public_key.hex()], limit=1)
        event = self.messenger.get_event(filters)
        if event:
            return Metadata.from_event(event)
        return None

    def update_metadata(self, name: Optional[str] = None, about: Optional[str] = None,
                       nip05: Optional[str] = None, picture: Optional[str] = None,
                       banner: Optional[str] = None, lud16: Optional[str] = None,
                       lud06: Optional[str] = None, username: Optional[str] = None,
                       display_name: Optional[str] = None, website: Optional[str] = None):
        """Update the client's metadata on Nostr relays.

        Args:
            name: Nostr name.
            about: Description or bio.
            nip05: NIP-05 identifier.
            picture: Profile picture URL.
            banner: Banner image URL.
            lud16: Lightning address.
            lud06: LNURL.
            username: Username.
            display_name: Display name.
            website: Website URL.
        """
        previous_metadata = self.get_metadata_for_pubkey(self.public_key)
        metadata = Metadata()
        if previous_metadata:
            metadata.set_metadata(previous_metadata.metadata_to_dict())
        if name:
            metadata.name = name
        if about:
            metadata.about = about
        if nip05:
            metadata.nip05 = nip05
        if picture:
            metadata.picture = picture
        if banner:
            metadata.banner = banner
        if lud16:
            metadata.lud16 = lud16
        if lud06:
            metadata.lud06 = lud06
        if username:
            metadata.username = username
        if display_name:
            metadata.display_name = display_name
        if website:
            metadata.website = website
        metadata.created_at = int(time.time())
        metadata.update()
        if previous_metadata and previous_metadata.content == metadata.content:
            print("No changes in metadata, skipping update.")
            return

        self.messenger.send_event(metadata.to_event())

    def send_direct_message(self, recipient_pubkey: str, message: str, event_ref: str = None):
        """Send an encrypted direct message to a recipient and wait for a response.

        Args:
            recipient_pubkey: The recipient's public key.
            message: The message content (string or dict, which will be JSON-encoded).
        """
        self.messenger.send_message(message=message, recipient_pubkey=recipient_pubkey, event_ref=event_ref)

    def send_direct_message_and_receive_response(self, recipient_pubkey: str, message: str, timeout: int = 30, event_ref: str = None) -> DecryptedMessage:
        """Send an encrypted direct message to a recipient and wait for a response.

        Args:
            recipient_pubkey: The recipient's public key.
            message: The message content (string or dict, which will be JSON-encoded).
        """
        return self.messenger.send_receive_message(message=message, recipient_pubkey=recipient_pubkey, timeout=timeout, event_ref=event_ref)

    def note_listener(self, callback: Callable[[Event], Any], pubkeys: List[str] = None, 
                     tags: List[str] = None, followers_only: bool = False, 
                     following_only: bool = False, timeout: int = 0, 
                     timestamp: int = None, close_after_first_message: bool = False):
        """Listen for public notes matching the given filters.

        Args:
            callback: Function to handle received notes (takes Event as argument).
            pubkeys: List of pubkeys to filter notes from (hex or bech32 format).
            tags: List of tags to filter notes by.
            followers_only: If True, only show notes from users the key follows (not implemented).
            following_only: If True, only show notes from users following the key (not implemented).
            timeout: Timeout for listening in seconds (0 for indefinite).
            timestamp: Filter messages since this timestamp (optional).
            close_after_first_message: Close subscription after receiving the first message.
        """

        authors = None
        if pubkeys:
            authors = [get_public_key(pk).hex() for pk in pubkeys]        
        filters = Filters(authors=authors, kinds=[EventKind.TEXT_NOTE],
                                since=timestamp or get_timestamp(), limit=10)
        if tags and len(tags) > 0:
            filters.add_arbitrary_tag("t", tags)

        thread = threading.Thread(target=self.messenger.event_listener, args=(filters, callback))
        thread.start()

    def direct_message_listener(self, callback: Callable[[Event, str], Any], recipient_pubkey: str = None,
                               timeout: int = 0, timestamp: int = None, close_after_first_message: bool = False):
        """Listen for incoming encrypted direct messages.

        Args:
            callback: Function to handle received messages (takes Event and message content as args).
            recipient_pubkey: Filter messages from a specific public key (optional).
            timeout: Timeout for listening in seconds (0 for indefinite).
            timestamp: Filter messages since this timestamp (optional).
            close_after_first_message: Close subscription after receiving the first message.
        """
        authors = [get_public_key(recipient_pubkey).hex()] if recipient_pubkey else None
        filters = Filters(authors=authors, kinds=[EventKind.ENCRYPTED_DIRECT_MESSAGE],
                                      since=timestamp or get_timestamp(), pubkey_refs=[self.public_key.hex()],
                                      limit=10)
        
        self.messenger.direct_message_listener(filters, callback)