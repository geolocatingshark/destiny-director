# Discord privileged-intent review — answers

Prepared for the October 2026 Message Content Intent re-authorisation of the
**beacon** bot (Destiny Director). Reuse and update this file for the next review.

**Facts the answers rely on** (check them before re-using an answer):

- Only beacon asks for a privileged intent, and only Message Content:
  `intents=h.Intents.ALL_UNPRIVILEGED | h.Intents.MESSAGE_CONTENT`
  (`dd/beacon/__main__.py`). Anchor uses `ALL_UNPRIVILEGED` alone. Neither bot asks for
  **Server Members** or **Presence**. The form lists them because it is generic; switch
  their toggles off in the Developer Portal if they are on.
- Message content is acted on in two places only:
  - **mirror** (`dd/beacon/extensions/mirror.py`): announcement channels in Kyber's
    server and the control server (`MirroredChannel` sources).
  - **nav-page feeds** (`dd/beacon/nav.py`, `extensions/free_games.py`,
    `extensions/twab.py`): the same team channels.

  Every other message is filtered out by channel ID, or by crosspost reference in
  `mirror_tracing.py`, without its content being looked at.
- Stored content is limited to `mirror_message_version`: snapshots of each delivered
  announcement version. They are pruned after `mirror_retention_days` (14), **except**
  the latest delivered post per destination channel, which is kept until a newer post
  replaces it. That exception is why "30 days or less" is answered **No**.
- Storage is Railway Postgres. Railway says all volume data is encrypted at rest.

Policy links (the prod repo, `main`):

- Privacy Policy: <https://github.com/geolocatingshark/destiny-director/blob/main/PRIVACY.md>
- Terms of Service: <https://github.com/geolocatingshark/destiny-director/blob/main/TOS.md>

---

## Application details

**What does your application do?**

> Destiny Director is the announcement bot for Kyber's Corner (kyberscorner.com), a
> Destiny 2 news and guide community. Kyber's team writes Destiny 2 news posts: weekly
> reset summaries, Xûr's inventory, Lost Sector and Trials rotations, Bungie's weekly
> news (TWAB), free game drops, and similar updates. The team publishes them in
> announcement channels in the Kyber's Corner server. Server admins anywhere can
> follow any of these feeds in one of their channels with `/autopost <feed>`. The bot
> then reposts each new post there, optionally pinging a role. It keeps the copies in
> sync: if the team corrects or withdraws a post, the bot edits or deletes every copy.
> Members can also call up the latest post on demand with commands such as `/xur`,
> `/ls today`, `/free games` and `/twid`.

**Do you have a public Privacy Policy?** Yes.

**Where is your Privacy Policy available?**

> In the Privacy Policy URL field of the app (shown on the bot's profile and on the
> invite screen), in the bot's About Me, and in our public GitHub repository:
> https://github.com/geolocatingshark/destiny-director/blob/main/PRIVACY.md

(Only claim what is true on the day you submit. Fill in the Privacy Policy URL and
Terms of Service URL fields under **General Information** in the Developer Portal,
and add the link to the bot's About Me, before submitting.)

**Link to your Privacy Policy:**
<https://github.com/geolocatingshark/destiny-director/blob/main/PRIVACY.md>

## Message Content Intent

**Can users opt out of having their message content tracked?** Yes.

> We never process the content of messages sent by members of the servers the bot is
> in. We act only on posts in our own team's announcement channels. A server admin
> can stop all delivery to their server at any time with
> `/autopost <feed> option:Disable`, or by removing the bot. Data about their server
> is deleted on request by email.

**Are you storing message content off-platform?** Yes.

**Are you storing user message content for 30 days or less?** No.

> We store no user message content. The only content we store is copies of our own
> team's announcements, kept so we can edit or delete the reposts. Copies are deleted
> after 14 days, except the latest post delivered to each following channel, which
> is kept until a newer post replaces it so it can still be corrected or withdrawn.

**How do users contact you to request deletion of their activity data?**

> By email to geolocatingshark@gmail.com, as stated in our Privacy Policy. We do not
> store message content from members' servers. The only messages we store are the
> announcements our own team publishes, mostly through our companion bot. For a
> server that follows a feed, we store only its server, channel and role IDs, and we
> delete them on request.

**Are you encrypting the data that you store at rest?** Yes.

> Our database is hosted on Railway, which encrypts all stored volume data at rest.

**Will message content be used to train ML or AI models?** No.

**Why do you need the Message Content intent?**

> Our core feature reposts announcements from the Kyber's Corner team to every
> server that follows them, and keeps those copies correct. To do this the bot has
> to read the text, embeds, attachments and components of new and edited posts in a
> small, fixed set of announcement channels that our team owns, in the Kyber's
> Corner server and our staff server. Without the intent, Discord delivers those
> posts to the bot with empty content, so there is nothing to repost and edits
> cannot be propagated. The same posts back our on-demand commands (`/free games`,
> `/twid`, and so on), which reply with the latest post from the matching channel.
> The bot filters every incoming message by channel ID before looking at it. Content
> from any other channel, in any other server, is never read, logged or stored.

**Screenshots / video** — one short screen recording works better than stills.
Record in a test server that follows a feed:

1. A post being published in one of the team's announcement channels.
2. The same post arriving in the following server's channel, with the role ping.
3. An edit to the original appearing in the copy.
4. `/autopost <feed> option:Enable` and then `option:Disable` (the opt-out).
5. `/free games` (or `/twid`) replying with the latest post from its channel.

Upload it somewhere viewable without signing in (an unlisted YouTube video or a
public Imgur album) and paste that link.
