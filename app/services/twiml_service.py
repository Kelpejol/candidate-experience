from twilio.twiml.voice_response import VoiceResponse



def build_handoff_twiml(hold_message: str, phone_number: str, status_callback_url: str) -> str:
    """
    Build TwiML for call handoff to a human agent.
    """
    response = VoiceResponse()
    response.say(hold_message)
    response.dial(
        phone_number, 
        answer_on_bridge=True, 
        record="record-from-answer-dual", 
        action=status_callback_url, 
        method="POST"
        )

    return str(response)