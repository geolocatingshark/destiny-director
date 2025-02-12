# Copyright © 2019-present gsfernandes81

# This file is part of "dd" henceforth referred to as "destiny-director".

# destiny-director is free software: you can redistribute it and/or modify it under the
# terms of the GNU Affero General Public License as published by the Free Software
# Foundation, either version 3 of the License, or (at your option) any later version.

# "destiny-director" is distributed in the hope that it will be useful, but WITHOUT ANY
# WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A
# PARTICULAR PURPOSE. See the GNU Affero General Public License for more details.

# You should have received a copy of the GNU Affero General Public License along with
# destiny-director. If not, see <https://www.gnu.org/licenses/>.

import logging
import traceback as tb
import typing as t

import hikari as h
import lightbulb as lb

from ...common import cfg
from ...common.schemas import UserCommand, db_session
from ...common.utils import FriendlyValueError
from ..bot import UserCommandBot

# TODO
# Add a way to try a command response

EMBEDS_FEATURE_FLAG = False

NOTE_ABOUT_SLOW_DISCORD_PROPAGATION = (
    "\nNote:\n"
    + "Discord propagates command changes slowly so it may take a few minutes for "
    + "changes to take effect."
)


@lb.add_checks(lb.checks.owner_only)
@lb.command(
    "command",
    "Custom command control",
    guilds=(cfg.control_discord_server_id,),
    inherit_checks=True,
    hidden=True,
)
@lb.implements(lb.SlashCommandGroup)
def command_group(ctx: lb.Context):
    pass


async def layer_name_autocomplete(
    option: h.AutocompleteInteractionOption, interaction: h.AutocompleteInteraction
) -> t.List[h.CommandOption] | None:
    name = option.name
    value = option.value
    # Get the list of all options provided
    other_options = interaction.options[0].options
    other_options = {
        other_option.name: other_option.value for other_option in other_options
    }

    l1_name = other_options.get("layer1")
    l2_name = other_options.get("layer2", "")
    l3_name = other_options.get("layer3", "")

    # If this is a layer name
    if name.startswith("layer"):
        try:
            depth = int(name[len("layer") :])
        except ValueError:
            # If the name is not layer followed by an int
            # do not autocomplete
            return

        # Get autocompletions from the db
        cmds = await UserCommand._autocomplete(l1_name, l2_name, l3_name)
        # Return names from the right layer depth
        options = [
            cmd.ln_names[depth - 1]
            for cmd in cmds
            if cmd.depth == depth and cmd.ln_names[depth - 1].startswith(value)
        ]
        return options


def layer_options(autocomplete: bool, postfix: str = ""):
    """Decorator to add layer options to command with optional autocompletion"""

    def decorator_actual(func):
        return lb.option(
            "layer3" + postfix,
            "1st layer commands and groups",
            autocomplete=autocomplete and layer_name_autocomplete,
            default="",
        )(
            lb.option(
                "layer2" + postfix,
                "1st layer commands and groups",
                autocomplete=autocomplete and layer_name_autocomplete,
                default="",
            )(
                lb.option(
                    "layer1" + postfix,
                    "1st layer commands and groups",
                    autocomplete=autocomplete and layer_name_autocomplete,
                )(func)
            )
        )

    return decorator_actual


def schema_options(
    type_needed,
    description_needed,
    command_groups_allowed=False,
    include_description=True,
):
    """Decorator to add non layer schema options to commands"""

    def decorator_actual(func):
        if EMBEDS_FEATURE_FLAG:
            choices = [
                h.CommandChoice(name="No Change", value=-1),
                h.CommandChoice(name="Text", value=1),
                h.CommandChoice(name="Message Copy", value=2),
                h.CommandChoice(name="Embed", value=3),
            ]
        else:
            choices = [
                h.CommandChoice(name="No Change", value=-1),
                h.CommandChoice(name="Text", value=1),
                h.CommandChoice(name="Message Copy", value=2),
            ]

        if command_groups_allowed:
            choices.append(h.CommandChoice(name="Command Group", value=0))
        if type_needed:
            choices = choices[1:]
            default = h.UNDEFINED
        else:
            default = -1

        if include_description:
            func = lb.option(
                "description",
                "Description of the command",
                default="",
                required=description_needed,
            )(func)

        func = lb.option(
            "type",
            "Type of response to show the user",
            choices=choices,
            default=default,
            type=int,
        )(func)

        func = lb.option("response", "Respond to the user with this data", default="")(
            func
        )

        return func

    return decorator_actual


@command_group.child
@schema_options(
    type_needed=True,
    description_needed=False,
    command_groups_allowed=False,
    include_description=False,
)
@lb.command(
    "preview",
    "Preview a command response prior to adding it",
    pass_options=True,
    inherit_checks=True,
    auto_defer=True,
)
@lb.implements(lb.SlashSubCommand)
async def preview_command(
    ctx: lb.Context,
    type: int,
    response: str,
):
    bot: UserCommandBot = ctx.bot
    type: int = int(type)

    try:
        cmd = UserCommand(
            "temp1",
            "temp2",
            "temp3",
            description="temp cmd preview",
            response_type=type,
            response_data=response,
        )
        response_func = bot._user_command_response_func_builder(cmd)
        await response_func.callback(ctx)

    except Exception as e:
        logging.exception(e)
        await ctx.respond(
            "An error occured while previewing the commmand."
            + "\n\n Error trace:\n```"
            + "\n".join(tb.format_exception(e))
            + "\n```"
            + (
                ("\nError message:\n" + "\n".join(e.args) + "\n")
                if isinstance(e, FriendlyValueError)
                else ""
            )
            + "\nRaw response data:\n```\n"
            + response
            + "\n```"
        )
    else:
        await ctx.respond(
            "Preview generated.\nIf no response is visible then please contact"
            + f" <@{(await bot.fetch_owner_ids())[-1]}>.\n\n"
            + "Raw response data:\n```\n"
            + response
            + "\n```"
        )


@command_group.child
@schema_options(type_needed=True, description_needed=True, command_groups_allowed=True)
@layer_options(autocomplete=False)
@lb.command(
    "add", "Add a command", pass_options=True, inherit_checks=True, auto_defer=True
)
@lb.implements(lb.SlashSubCommand)
async def add_command(
    ctx: lb.Context,
    layer1: str,
    layer2: str,
    layer3: str,
    description: str,
    type: int,
    response: str,
):
    bot: UserCommandBot = ctx.bot
    type: int = int(type)

    try:
        async with db_session() as session:
            async with session.begin():
                await UserCommand.add_command(
                    layer1,
                    layer2,
                    layer3,
                    description=description,
                    response_type=type,
                    response_data=response,
                    session=session,
                )
                await bot.sync_application_commands(session=session)
    except Exception as e:
        logging.exception(e)
        await ctx.respond(
            "An error occured adding the `{}` commmand or group.\n".format(
                " -> ".join(
                    [layer for layer in [layer1, layer2, layer3] if layer != ""]
                )
            )
            + (
                ("\nError message:\n" + "\n".join(e.args) + "\n")
                if isinstance(e, FriendlyValueError)
                else ""
            )
            + "\n Error trace:\n```"
            + "\n".join(tb.format_exception(e))
            + "\n```"
        )
    else:
        await ctx.respond(
            "Successfully added the `"
            + " -> ".join([layer for layer in [layer1, layer2, layer3] if layer != ""])
            + "` command.\n"
            + NOTE_ABOUT_SLOW_DISCORD_PROPAGATION
        )


@command_group.child
@lb.option(
    "delete_whole_group",
    "USE WITH CAUTION, DELETES ALL SUBCOMMANDS",
    bool,
    default=False,
)
@layer_options(autocomplete=True)
@lb.command(
    "delete",
    "Delete a command",
    pass_options=True,
    inherit_checks=True,
    auto_defer=True,
)
@lb.implements(lb.SlashSubCommand)
async def delete_command(
    ctx: lb.Context,
    layer1: str,
    layer2: str,
    layer3: str,
    delete_whole_group: bool = False,
):
    bot: UserCommandBot = ctx.bot

    try:
        # Delete the command and sync
        if delete_whole_group:
            deleted_commands = await UserCommand.delete_command_group(
                layer1, layer2, cascade=True
            )
        else:
            deleted_commands = (
                await UserCommand.delete_command(layer1, layer2, layer3),
            ) or await UserCommand.delete_command_group(layer1, layer2, layer3)
        await bot.sync_application_commands()
    except Exception as e:
        # If an exception occurs, respond with it as a message
        logging.exception(e)
        await ctx.respond(
            "An error occured deleting the `{}` commmand or group.\n".format(
                " -> ".join(
                    [layer for layer in [layer1, layer2, layer3] if layer != ""]
                )
            )
            + (
                ("\nError message:\n" + "\n".join(e.args) + "\n")
                if isinstance(e, FriendlyValueError)
                else ""
            )
            + "\n Error trace:\n```"
            + "\n".join(tb.format_exception(e))
            + "\n```"
        )
    else:
        # Otherwise confirm success
        if deleted_commands:
            await ctx.respond(
                "Deleted the following command(s):\n```"
                + "\n".join(str(cmd) for cmd in deleted_commands)
                + "\n```"
                + NOTE_ABOUT_SLOW_DISCORD_PROPAGATION
            )
        else:
            await ctx.respond(
                "`{}` command or group not found".format(
                    " -> ".join(
                        [layer for layer in [layer1, layer2, layer3] if layer != ""]
                    )
                )
            )


@command_group.child
@schema_options(type_needed=False, description_needed=False)
@layer_options(autocomplete=True)
@lb.command(
    "edit", "Edit a command", pass_options=True, inherit_checks=True, auto_defer=True
)
@lb.implements(lb.SlashSubCommand)
async def edit_command(
    ctx: lb.Context,
    layer1: str,
    layer2: str,
    layer3: str,
    description: str,
    type: int,
    response: str,
):
    bot: UserCommandBot = ctx.bot
    type: int = int(type)

    try:
        async with db_session() as session:
            async with session.begin():
                # Delete subject command from db
                deleted_command = await UserCommand.delete_command(
                    layer1, layer2, layer3, session=session
                )

                # Update command parameters if specified
                ln_names = deleted_command.ln_names
                description = (
                    description if description else deleted_command.description
                )
                type = type if type != -1 else deleted_command.response_type
                response = response if response else deleted_command.response_data

                # Add command back with new parameters
                await UserCommand.add_command(
                    *ln_names,
                    description=description,
                    response_type=type,
                    response_data=response,
                    session=session,
                )

                # Resync with discord
                await bot.sync_application_commands(session=session)
    except Exception as e:
        logging.exception(e)
        await ctx.respond(
            "An error occured adding the `{}` commmand or group.\n".format(
                " -> ".join(
                    [layer for layer in [layer1, layer2, layer3] if layer != ""]
                )
            )
            + (
                ("\nError message:\n" + "\n".join(e.args) + "\n")
                if isinstance(e, FriendlyValueError)
                else ""
            )
            + "\n Error trace:\n```"
            + "\n".join(tb.format_exception(e))
            + "\n```"
        )
    else:
        await ctx.respond(
            "Successfully edited the `"
            + " -> ".join([layer for layer in [layer1, layer2, layer3] if layer != ""])
            + "` command.\n"
            + NOTE_ABOUT_SLOW_DISCORD_PROPAGATION
        )


@command_group.child
@layer_options(autocomplete=True, postfix="new")
@layer_options(autocomplete=True)
@lb.command(
    "rename",
    "Rename a command or command group",
    pass_options=True,
    inherit_checks=True,
    auto_defer=True,
)
@lb.implements(lb.SlashSubCommand)
async def rename_command_or_group(
    ctx: lb.Context,
    layer1: str,
    layer2: str,
    layer3: str,
    layer1new: str,
    layer2new: str,
    layer3new: str,
):
    bot: UserCommandBot = ctx.bot

    try:
        async with db_session() as session:
            async with session.begin():
                # Delete subject command or group from db
                deleted_commands: t.List[UserCommand] = []

                # If layer3 is not specified then try and rename any groups
                # as well since there might be one specified
                deleted_commands.extend(
                    await UserCommand.delete_command_group(
                        layer1, layer2, cascade=True, session=session
                    )
                    if not layer3
                    else []
                )

                deleted_commands.append(
                    await UserCommand.delete_command(
                        layer1, layer2, layer3, session=session
                    )
                )

                added_commands = []
                for deleted_command in deleted_commands:
                    if not deleted_command:
                        continue
                    # Add commands back with new parameters
                    added_commands.append(
                        await UserCommand.add_command(
                            layer1new or deleted_command.l1_name,
                            layer2new or deleted_command.l2_name,
                            layer3new or deleted_command.l3_name,
                            description=deleted_command.description,
                            response_type=deleted_command.response_type,
                            response_data=deleted_command.response_data,
                            session=session,
                        )
                    )

                # Resync with discord
                await bot.sync_application_commands(session=session)
    except Exception as e:
        # If an exception occurs, respond with it as a message
        logging.exception(e)
        await ctx.respond(
            "An error occured renaming the `{}` to `{}`.\n".format(
                " -> ".join(
                    [layer for layer in [layer1, layer2, layer3] if layer != ""]
                ),
                " -> ".join(
                    [
                        layer
                        for layer in [layer1new, layer2new, layer3new]
                        if layer != ""
                    ]
                ),
            )
            + ("\nError message:\n" + "\n".join(e.args) + "\n")
            if isinstance(e, FriendlyValueError)
            else ""
            + "\n Error trace:\n```"
            + "\n".join(tb.format_exception(e))
            + "\n```"
        )
    else:
        # Otherwise confirm success
        await ctx.respond(
            "Renamed:\n"
            + "\n".join(
                [
                    "`{}`  **to**  `{}`".format(deleted_command, added_command)
                    for deleted_command, added_command in zip(
                        deleted_commands, added_commands
                    )
                ]
            )
            + NOTE_ABOUT_SLOW_DISCORD_PROPAGATION
        )


def register(bot):
    for command in [
        command_group,
    ]:
        bot.command(command)
