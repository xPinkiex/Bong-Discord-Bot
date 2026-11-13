"""Conversation handling: history, detection, prompt building and replying.

    turn.py        Turn — one incoming message, discord-free
    history.py     Per-channel rolling history with summaries
    detector.py    Decides whether a message is meant for Bong
    prompt.py      Builds the LLM messages from pluggable sections
    bong.py        Bong — ties it all together and produces replies
"""
