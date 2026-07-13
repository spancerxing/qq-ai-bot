"""QQ Bot WebSocket intent flags.

See: https://bot.q.qq.com/wiki/develop/api-v2/dev-prepare/interface-framework/event-emit.html
"""

# GROUP_AND_C2C_EVENT — this single bit enables both:
#   - GROUP_AT_MESSAGE_CREATE (group @, group all messages)
#   - C2C_MESSAGE_CREATE (private/direct messages)
#   - FRIEND_ADD, GROUP_ADD_ROBOT, etc.
GROUP_AND_C2C = 1 << 25
