import datetime
import re
import discord
import lavalink
from discord.ext import commands

url_rx = re.compile(r'https?://(?:www\.)?.+')


class Music(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

        if not hasattr(bot, 'lavalink'):  # This ensures the client isn't overwritten during cog reloads.
            bot.lavalink = lavalink.Client(bot.user.id)
            bot.lavalink.add_node('127.0.0.1', 2333, 'youshallnotpass', 'eu',
                                  'default-node')  # Host, Port, Password, Region, Name
            bot.add_listener(bot.lavalink.voice_update_handler, 'on_socket_response')

        lavalink.add_event_hook(self.track_hook)

    def cog_unload(self):
        """ Cog unload handler. This removes any event hooks that were registered. """
        self.bot.lavalink._event_hooks.clear()

    async def cog_before_invoke(self, ctx):
        """ Command before-invoke handler. """
        guild_check = ctx.guild is not None
        #  This is essentially the same as `@commands.guild_only()`
        #  except it saves us repeating ourselves (and also a few lines).

        if guild_check:
            await self.ensure_voice(ctx)
            #  Ensure that the bot and command author share a mutual voicechannel.

        return guild_check

    async def cog_command_error(self, ctx, error):
        if isinstance(error, commands.CommandInvokeError):
            await ctx.send(error.original)
            # The above handles errors thrown in this cog and shows them to the user.
            # This shouldn't be a problem as the only errors thrown in this cog are from `ensure_voice`
            # which contain a reason string, such as "Join a voicechannel" etc. You can modify the above
            # if you want to do things differently.

    async def ensure_voice(self, ctx):
        """ This check ensures that the bot and command author are in the same voicechannel. """
        player = self.bot.lavalink.player_manager.create(ctx.guild.id, endpoint=str(ctx.guild.region))
        # Create returns a player if one exists, otherwise creates.
        # This line is important because it ensures that a player always exists for a guild.

        # Most people might consider this a waste of resources for guilds that aren't playing, but this is
        # the easiest and simplest way of ensuring players are created.

        # These are commands that require the bot to join a voicechannel (i.e. initiating playback).
        # Commands such as volume/skip etc don't require the bot to be in a voicechannel so don't need listing here.
        should_connect = ctx.command.name in ('play', 'summon')

        if not ctx.author.voice or not ctx.author.voice.channel:
            # Our cog_command_error handler catches this and sends it to the voicechannel.
            # Exceptions allow us to "short-circuit" command invocation via checks so the
            # execution state of the command goes no further.
            raise commands.CommandInvokeError('Join a voicechannel first.')

        if not player.is_connected:
            if not should_connect:
                raise commands.CommandInvokeError('Not connected.')

            permissions = ctx.author.voice.channel.permissions_for(ctx.me)

            if not permissions.connect or not permissions.speak:  # Check user limit too?
                raise commands.CommandInvokeError('I need the `CONNECT` and `SPEAK` permissions.')

            player.store('channel', ctx.channel.id)
            await ctx.guild.change_voice_state(channel=ctx.author.voice.channel)
        else:
            if int(player.channel_id) != ctx.author.voice.channel.id:
                raise commands.CommandInvokeError('You need to be in my voicechannel.')

    async def track_hook(self, event):
        if isinstance(event, lavalink.events.QueueEndEvent):
            # When this track_hook receives a "QueueEndEvent" from lavalink.py
            # it indicates that there are no tracks left in the player's queue.
            # To save on resources, we can tell the bot to disconnect from the voicechannel.
            guild_id = int(event.player.guild_id)
            guild = self.bot.get_guild(guild_id)
            await guild.change_voice_state(channel=None)

    @commands.command(name="play", aliases=['p'])
    async def play(self, ctx, *, query: str):
        """Searches and plays a song from a given query."""
        # Get the player for this guild from cache.
        player = self.bot.lavalink.player_manager.get(ctx.guild.id)
        # Remove leading and trailing <>. <> may be used to suppress embedding links in Discord.
        query = query.strip('<>')

        # Check if the user input might be a URL. If it isn't, we can lavalink do a YouTube search for it instead.
        # SoundCloud searching is possible by prefixing "scsearch:" instead.
        if not url_rx.match(query):
            query = f'ytsearch:{query}'

        # Get the results for the query from lavalink.
        results = await player.node.get_tracks(query)

        # Results could be None if lavalink returns an invalid response (non-JSON/non-200 (OK)).
        # ALternatively, resullts['tracks'] could be an empty array if the query yielded no tracks.
        if not results or not results['tracks']:
            return await ctx.send('Nothing found!')

        embed = discord.Embed(color=discord.Color.blurple())

        # Valid loadTypes are:
        #   TRACK_LOADED    - single video/direct URL)
        #   PLAYLIST_LOADED - direct URL to playlist)
        #   SEARCH_RESULT   - query prefixed with either ytsearch: or scsearch:.
        #   NO_MATCHES      - query yielded no results
        #   LOAD_FAILED     - most likely, the video encountered an exception during loading.
        if results['loadType'] == 'PLAYLIST_LOADED':
            tracks = results['tracks']

            for track in tracks:
                # Add all of the tracks from the playlist to the queue.
                player.add(requester=ctx.author.id, track=track)

            embed.title = 'Playlist Enqueued!'
            embed.description = f'{results["playlistInfo"]["name"]} - {len(tracks)} tracks'
        else:
            track = results['tracks'][0]
            embed.title = 'Track Enqueued'
            embed.description = f'[{track["info"]["title"]}]({track["info"]["uri"]})'

            # You can attach additional information to audiotracks through kwargs, however this involves
            # constructing the AudioTrack class yourself.
            track = lavalink.models.AudioTrack(track, ctx.author.id)
            player.add(requester=ctx.author.id, track=track)

        await ctx.send(embed=embed)

        # We don't want to call .play() if the player is playing as that will effectively skip
        # the current track.
        if not player.is_playing:
            await player.play()

    @commands.command(name="disconnect", aliases=['dc'])
    async def disconnect(self, ctx):
        """Disconnects the player from the voice channel and clears its queue."""
        player = self.bot.lavalink.player_manager.get(ctx.guild.id)

        if not player.is_connected:
            # We can't disconnect, if we're not connected.
            return await ctx.send('im not connekted to any voice channel men')

        if not ctx.author.voice or (player.is_connected and ctx.author.voice.channel.id != int(player.channel_id)):
            # Abuse prevention. Users not in voice channels, or not in the same voice channel as the bot
            # may not disconnect the bot.
            return await ctx.send('y u try to disconnect me when ur not even listening men?')

        # Clear the queue to ensure old tracks don't start playing
        # when someone else queues something.
        player.queue.clear()
        # Stop the current track so lavalink consumes less resources.
        await player.stop()
        # Disconnect from the voice channel.
        await ctx.guild.change_voice_state(channel=None)
        await ctx.message.add_reaction('👍')

    @commands.command(name="stop", aliases=['pause'])
    async def stop(self, ctx):
        """Stops the player"""
        player = self.bot.lavalink.player_manager.get(ctx.guild.id)

        if player.paused:
            await ctx.send("im already paused u nuberman")
        else:
            await player.set_pause(True)
            await ctx.message.add_reaction('👍')

    @commands.command(name="resume")
    async def resume(self, ctx):
        """Resumes the player"""
        player = self.bot.lavalink.player_manager.get(ctx.guild.id)

        if player.paused:
            await ctx.message.add_reaction('👍')
            await player.set_pause(False)
        else:
            await ctx.send("men i playing musik already u nub")

    @commands.command(name="queue", aliases=["q"])
    async def queue(self, ctx):
        """Shows current queue"""
        player = self.bot.lavalink.player_manager.get(ctx.guild.id)
        embed = discord.Embed(colour=discord.Colour(0xa3cf32), timestamp=datetime.datetime.now()) \
            .set_author(name='| ' + ctx.guild.name + '\'s Queue', url="", icon_url=ctx.guild.icon_url) \
            .set_footer(text="Requested by " + ctx.author.name, icon_url=ctx.author.avatar_url) \

        if len(player.queue) == 0:
            if player.is_playing:
                track = player.current.title
                url = 'https://www.youtube.com/watch?v=' + player.current.identifier
                embed.add_field(name="Now playing", value=f'[{track}]({url})', inline=False)
                await ctx.send(embed=embed)
            else:
                embed.set_author(name='| ' + ctx.guild.name + '\'s queue is empty :/', icon_url=ctx.guild.icon_url)
                await ctx.send(embed=embed)
        else:
            queue = player.queue
            current = player.current.title
            i = 0
            url = 'https://www.youtube.com/watch?v=' + player.current.identifier

            embed.add_field(name="Now playing", value=f'[{current}]({url})', inline=False)
            while i < len(queue):
                url = 'https://www.youtube.com/watch?v=' + queue[i].identifier
                title = queue[i].title

                embed.add_field(name=str(i + 1) + ".", value=f'[{title}]({url})', inline=False)
                i += 1

            await ctx.send(embed=embed)

    @commands.command(name="skip")
    async def skip(self, ctx):
        """Skips the current song playing"""
        player = self.bot.lavalink.player_manager.get(ctx.guild.id)
        await player.skip()
        await ctx.message.add_reaction('👍')

    @commands.command(name="clear")
    async def clear(self, ctx):
        """Clears the current queue and stops player"""
        player = self.bot.lavalink.player_manager.get(ctx.guild.id)
        await player.stop()
        player.queue.clear()
        await ctx.message.add_reaction('👍')

    @commands.command(name="summon")
    async def summon(self, ctx):
        """Summons player to your channel"""
        player = self.bot.lavalink.player_manager.get(ctx.guild.id)

        if player.is_connected:
            await ctx.send("men im already in vc")
        else:
            await ctx.guild.change_voice_state(channel=ctx.author.voice.channel)

    @commands.command(name="volume")
    async def volume(self, ctx, args=None):
        """Sets player volume (1-1000)"""
        player = self.bot.lavalink.player_manager.get(ctx.guild.id)

        if args is None:
            await ctx.send("Current player volume is " + str(player.volume))
        else:
            args = int(args)
            if 0 <= args <= 1000:
                await player.set_volume(args)
                await ctx.send("Set player volume to " + str(args))
            else:
                await ctx.send("The volume can only be between 0-1000")

    @commands.command(name="seek")
    async def seek(self, ctx, args):
        """Seeks to a given point in the track (HH:MM:SS format)"""
        player = self.bot.lavalink.player_manager.get(ctx.guild.id)
        h, m, s, = args.split(':')
        time = (int(h) * 3600 + int(m) * 60 + int(s)) * 1000

        await player.seek(time)

    @commands.command(name="np")
    async def np(self, ctx):
        """Returns position in track"""
        player = self.bot.lavalink.player_manager.get(ctx.guild.id)
        position = round(player.position / 1000)

        await ctx.send(datetime.timedelta(seconds=position))


def setup(bot):
    bot.add_cog(Music(bot))
