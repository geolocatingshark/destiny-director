# Privacy Policy

_Last updated: 25 October 2026_

This policy covers the Destiny Director Discord bot, the companion bot its team uses
to publish posts, and the staff-only web panel that goes with them (together, "the
bot"). It is operated by GS ("we", "us"). You can reach us at
geolocatingshark@gmail.com.

**In short:** the bot does not collect personal data about the members of servers it
is in, and it does not read, keep or analyse the messages people send in your server.
It stores the server, channel and role IDs it needs to deliver the posts you follow,
and short-lived copies of the announcements it delivers. Those announcements are
written by the Kyber's Corner team.

## What the bot collects

### Servers that use the bot

When a server admin turns on a feed with `/autopost`, we store:

- the server ID and the channel ID the feed posts to;
- the ID of the role to ping, if the admin chose one;
- delivery records for each post sent to that channel: the message IDs, whether the
  post was delivered, and timestamps. These let us edit or remove a post later if the
  original is corrected or withdrawn.

We also keep a member **count** for each server the bot is in, which we use to decide
the order servers receive posts in. We do not store who those members are.

### Usage statistics

We count how many times each slash command is used per day, and how many servers
follow each feed. These counts are not linked to any user or server.

### What the bot does **not** collect

We do not collect or store usernames, user IDs of server members, member lists,
presence or activity status, direct messages, voice data, IP addresses, or the
content of messages people send in your server.

## Message content

Discord gives the bot access to message content so that it can read posts in the
handful of announcement and news channels run by the Kyber's Corner team, in the
Kyber's Corner Discord server and our own staff server. The bot uses that access for:

- **Reposting announcements.** When the team publishes a post in one of those
  channels, the bot copies it to every server that follows that feed, and applies any
  later edit or deletion to the copies.
- **Answering commands.** Commands such as `/free games` or the weekly-news command
  reply with the latest post from the matching team channel.

Discord also delivers messages from other servers to the bot. The bot checks only
which channel a message came from (and, for Discord's "follow channel" crossposts,
which channel it was crossposted from). It ignores everything else. The content of
those messages is never used, logged or saved. The only trace is a short-lived memory
cache (Discord's library keeps the last 100 messages the bot received), which is
cleared as new messages arrive and whenever the bot restarts.

### Copies of announcements

To keep the reposts in sync and give the team a history of what was sent, we store a
copy of each version of each announcement the bot delivers (its text, embeds and
attachment links). These copies are deleted after **14 days**. The exception is the
most recent post delivered to each following channel: we keep it, and its copy, until
a newer post replaces it, so that it can still be corrected or removed. All of these
copies are of posts written by the Kyber's Corner team, never by members of your
server.

## Staff web panel and Bungie access

Only the bot's owners can sign in to the staff web panel. Sign-in uses Discord
("identify" permission only): we read your Discord user ID to check you are an owner,
then set a signed cookie that lasts 30 days. We do not keep your Discord access token.
If someone who is not an owner tries to sign in, their Discord user ID is written to
our server logs so we can spot misuse.
We store the user ID of the staff member who created each draft post, and delete
drafts after 14 days.

To read game data from Bungie, the bot keeps a Bungie sign-in token for a staff-owned
Bungie account. No other Bungie account is ever linked.

## Where data is stored and who can access it

Data is stored in a private PostgreSQL database hosted by
[Railway](https://railway.com). Railway encrypts this data at rest. Only the bot's
developers can access it, and only to develop, test and run the bot. We do not sell
data or share it with anyone, except where the law requires it.

The bot also contacts Discord, to run, and Bungie, to fetch public game data. Bungie
receives no data about your server or its members from us. Error reports are posted
to a private Discord channel that only the developers can see.

Data is never used to train machine learning or AI models.

If data is ever accessed without authorisation, we will announce it in the Kyber's
Corner Discord server, and in each affected server where the bot can still post.

## Your choices and rights

- **Stop a feed:** a server admin can run `/autopost <feed> option:Disable`, which
  stops all further posts to that channel. The channel's settings are kept, switched
  off, so the feed can be turned back on later.
- **Remove the bot:** removing the bot from your server stops all posts and all
  access to your server. The IDs listed above stay stored until you ask us to delete
  them.
- **See or delete your data:** email geolocatingshark@gmail.com from an account that
  can show you administer the server, and we will send you what we hold about it or
  delete it. We aim to reply within 30 days.

## Children

The bot is not for children under 13, or under the age of digital consent in their
country, unless a parent or legal guardian agrees to the
[Terms of Service](TOS.md) and this policy for them, in line with local law and
Discord's Terms of Service.

## Changes

If we change this policy, we will update the date at the top and announce the change
in the Kyber's Corner Discord server.

## Contact

Questions or concerns: geolocatingshark@gmail.com
