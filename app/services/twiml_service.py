"""Generates Twilio TwiML documents used to control in-progress outbound calls.

Currently covers handing an active call off from the voice agent to a
live human agent.
"""

from twilio.twiml.voice_response import VoiceResponse



def build_handoff_twiml(hold_message: str, phone_number: str, status_callback_url: str) -> str:
    """
    Build TwiML for call handoff to a human agent.

    Params:
        hold_message: spoken to the candidate before bridging to the agent.
        phone_number: human agent number Twilio dials into the call.
        status_callback_url: URL Twilio POSTs the dial's outcome to.

    Returns the TwiML document as a string. Pure XML generation, no side
    effects (the actual call/dial is executed by Twilio when it receives
    this document).
    """
    response = VoiceResponse()
    response.say(hold_message)
    response.dial(
        phone_number,
        answer_on_bridge=True,  # only bridge audio once the agent answers, so the candidate isn't connected to dead air/ringback
        record="record-from-answer-dual",  # record both call legs, starting from when the agent answers
        action=status_callback_url,  # Twilio POSTs the dial result (e.g. completed/no-answer/busy) here after the dial ends
        method="POST"
        )

    return str(response)