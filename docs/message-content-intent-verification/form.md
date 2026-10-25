# Request Intents Review — questions and answers

The Discord Developer Portal's **Request Intents Review** form for Destiny Director
(app ID `986338748813934642`), as filled in on 25 October 2026. Transcribed from a
print of the form. The print cuts the long text boxes off where they scroll, so their
full text is taken from the drafts they were pasted from.

## Application details

**What does your application do?** _Please be as detailed as possible, and feel free to
include links to image or video examples._

> Destiny Director is the announcement bot for Kyber's Corner (kyberscorner.com), a
> Destiny 2 news and guide community. Kyber's team writes Destiny 2 news posts: weekly
> reset summaries, Xûr's inventory, Lost Sector and Trials rotations, Bungie's weekly
> news (TWAB), free game drops, and similar updates. The team publishes them in
> announcement channels in the Kyber's Corner server. Server admins anywhere can follow
> any of these feeds in one of their channels with /autopost <feed>. The bot then
> reposts each new post there, optionally pinging a role. It keeps the copies in sync:
> if the team corrects or withdraws a post, the bot edits or deletes every copy.
> Members can also call up the latest post on demand with commands such as /xur,
> /ls today, /free games and /twid.

**Do you have a public Privacy Policy telling your users about their data usage?**

> Yes

**Where is your Privacy Policy available?** _Some examples include a link within your
bot bio, a help command, a rules channel, a DM sent to users upon guild join, or any
other form of notification._

> In the Privacy Policy URL field of the app (shown on the bot's profile and on the
> invite screen), in the bot's About Me, and in our public GitHub repository:
> https://github.com/geolocatingshark/destiny-director/blob/main/PRIVACY.md

**Please share a link to your Privacy Policy.**

> https://github.com/geolocatingshark/destiny-director/blob/main/PRIVACY.md

## Privileged Gateway Intents

**You are applying for reauthorization for the following intents:**

- [ ] Server Members Intent
- [ ] Presence Intent
- [x] Message Content Intent

## Message Content Intent

**Can users opt-out of having their message content data tracked?**

> Yes

(The form shows no text box for this answer. The explanation we drafted for it, kept
for reference: "We never process the content of messages sent by members of the
servers the bot is in. We act only on posts in our own team's announcement channels. A
server admin can stop all delivery to their server at any time with /autopost <feed>
option:Disable, or by removing the bot. Data about their server is deleted on request
by email.")

**Are you storing message content data off-platform (outside of Discord)?**

> Yes

**Are you storing user message content data for 30 days or less?**

> No

(Also no text box. Our reasoning: we store no user message content. The only content
we store is copies of our own team's announcements, deleted after 14 days except the
latest post delivered to each following channel, which is kept until a newer post
replaces it.)

**How do users contact you to request deletion of their activity data?**

> By email to geolocatingshark@gmail.com, as stated in our Privacy Policy. We do not
> store message content from members' servers. The only messages we store are the
> announcements our own team publishes, mostly through our companion bot. For a server
> that follows a feed, we store only its server, channel and role IDs, and we delete
> them on request.

**Are you encrypting the data that you store at rest, as is required by our developer
policy?**

> Yes

**Will the message content data be used to train machine learning or AI Models?** _If
yes, please explain how in the text box below, including how such models would be
used._

> No

**Why do you need the Message Content intent?** _Please describe the feature you're
building using this intent with as much detail as possible._

> Our core feature reposts announcements from the Kyber's Corner team to every server
> that follows them, and keeps those copies correct. To do this the bot has to read the
> text, embeds, attachments and components of new and edited posts in a small, fixed
> set of announcement channels that our team owns, in the Kyber's Corner server and our
> staff server. Without the intent, Discord delivers those posts to the bot with empty
> content, so there is nothing to repost and edits cannot be propagated. The same posts
> back our on-demand commands (/free games, /twid, and so on), which reply with the
> latest post from the matching channel. The bot filters every incoming message by
> channel ID before looking at it. Content from any other channel, in any other server,
> is never read, logged or stored.

**Please provide links to screenshots and/or videos that demonstrate your use case.**
_For each selected intent, we require you to provide a link to screenshots or video
that demonstrates your desired use case working within a Discord server._

> Still empty when the form was printed. The screenshots are in
> [`screenshots/`](screenshots), described in the [README](README.md#screenshots):
> https://github.com/geolocatingshark/destiny-director/tree/main/docs/message-content-intent-verification/screenshots

## Acknowledgement

- [x] By clicking Submit, you certify that the information provided above is accurate
  and complete, and that you and your app are in compliance with Discord's Developer
  Terms of Service and Developer Policy. If this app is owned or operated by an entity,
  you confirm that you have the authority to certify on its behalf.
