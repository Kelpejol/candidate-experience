# DIDWW + ElevenLabs Telephony Plan

This note explains the planned telephony architecture for the Candidate Experience Calling Agent, using DIDWW for the Nigerian phone number/SIP trunking and ElevenLabs for the AI voice agent.

## Current Decision

DIDWW is a viable telephony provider for the pilot because support confirmed:

- The Nigerian `+234-201` DID can be used for inbound calls.
- The original caller ID should be passed to us in the SIP INVITE, unless the caller masks it or an upstream carrier changes it.
- The same DID can be used for outbound calls once DIDWW outbound trunk services are enabled and configured.
- Nigerian DIDs support local calling, so outbound calls to Nigeria should be passed locally.
- Inbound and outbound services are separate and do not share concurrency limits.
- DIDWW does not provide call recording on this setup.
- DIDWW can provide CDR/call events by webhook after services are active and support enables it.

## Target Architecture

### Inbound Calls

```text
Candidate calls +234-201 DID
        |
        v
DIDWW inbound SIP trunk
        |
        v
ElevenLabs / ElevenAgents
        |
        v
AI answers the candidate
        |
        v
ElevenLabs post-call webhook
        |
        v
Our FastAPI service
        |
        v
SQLite now, production DB later
```

What we expect to store from the ElevenLabs webhook:

- ElevenLabs conversation ID
- caller ID / candidate phone, if present
- called number
- transcript
- summary
- disposition, such as `answered_by_ai` or `handed_off_to_human`
- call start/end time
- AI recording reference if ElevenLabs exposes it

### Outbound Calls

```text
Existing app or our backend triggers outbound call
        |
        v
ElevenLabs outbound agent call
        |
        v
DIDWW outbound SIP trunk
        |
        v
Nigerian local route
        |
        v
Candidate phone
```

DIDWW support clarified that Nigerian DIDs support local calling, so this should work for outbound Nigeria calls once outbound trunking is enabled.

### Human Handoff

```text
Candidate speaks with AI
        |
        v
AI decides human support is needed
        |
        v
ElevenLabs transfer_to_number or SIP transfer
        |
        v
3CX officer / 3CX queue
```

For the pilot, ElevenLabs should handle the handoff. We should keep our old Twilio handoff code parked, but it is not part of the main path anymore.

## Does One DIDWW Purchase Cover Both Inbound and Outbound?

The DID purchase gives us the Nigerian number and inbound capability, but outbound calling also requires DIDWW outbound trunk service to be enabled and configured.

Think of it as:

```text
DID number rental = owns/receives calls on the +234 number
Inbound trunk = routes inbound calls to ElevenLabs
Outbound trunk = lets ElevenLabs place calls through DIDWW
Outbound route/rates = determines cost and delivery for calls to Nigeria
```

So the same number can be used for both inbound and outbound caller ID, but outbound is not just the DID purchase by itself. It needs outbound trunk setup.

## Concurrency

DIDWW support explained two separate concurrency areas.

### Inbound Concurrency

When buying/configuring the DID, we choose how many inbound channels the number can support.

Two common models:

- Dedicated channels: pay a monthly fee for a fixed number of concurrent calls, for example 2 channels means only 2 calls at the same time.
- Pay-per-minute/metred channel option: usually provides a larger pool, support mentioned around 100 metered channels.

If calls exceed the channel limit:

```text
existing calls continue
new incoming calls fail or cannot connect
candidate may hear failure/busy/drop depending on carrier behavior
```

### Outbound Concurrency

Outbound concurrency is configured on the outbound trunk.

DIDWW said outbound has no fixed default limit in the same way. When creating the outbound trunk, we set a capacity limit for how many concurrent outbound calls we allow per trunk.

### Are Inbound and Outbound Limits Shared?

No. DIDWW support said inbound and outbound calls are separate services.

That means:

```text
Inbound traffic should not consume outbound trunk capacity.
Outbound campaign calls should not consume inbound DID channels.
```

ElevenLabs may also have its own concurrency limits depending on the plan/account. That must be checked separately in ElevenLabs before production scale.

## Does The Number Work Only For Nigerian Calls?

The `+234-201` DID is a Nigerian local-style number. It is best for Nigerian candidate experience because:

- candidates see a Nigerian number
- inbound callers are more likely to trust it
- outbound caller ID looks local
- DIDWW now says outbound to Nigeria should be routed locally

It may still be reachable from international callers if their carrier can route to Nigerian numbers, but the production assumption should be:

```text
Primary use case: Nigeria inbound and Nigeria outbound.
International calls: possible, but not the main use case and should be tested separately.
```

If international support becomes important, test calls from at least a few target countries before promising it.

## Recording Plan

DIDWW does not provide recording for this setup. DIDWW is acting as the carrier/routing layer.

Likely recording ownership:

```text
AI part of call: ElevenLabs recording/audio/transcript
Human handoff part: 3CX recording
Carrier metadata: DIDWW CDR/call events
```

Our backend should eventually store separate references:

```text
elevenlabs_conversation_id
elevenlabs_audio_url or audio reference
3cx_call_id
3cx_recording_url
didww_call_id / CDR ID
```

Important pilot test:

```text
When AI transfers to 3CX, confirm whether ElevenLabs audio covers only the AI conversation or also includes the handoff.
Confirm whether 3CX records the human part reliably.
```

## What Are CDR / Call Events Webhooks For?

CDR means Call Detail Record.

DIDWW CDR/call events are not recordings. They are carrier-level metadata about calls.

They are useful for:

- confirming an inbound call reached DIDWW
- confirming DIDWW forwarded the call to ElevenLabs
- confirming outbound calls were attempted
- knowing whether outbound calls connected, failed, were busy, or were unanswered
- storing carrier call duration
- reconciling billing
- debugging SIP failures
- matching carrier events with ElevenLabs conversation records

Useful fields usually include:

- caller ID
- called number
- direction
- start time
- answer time
- end time
- duration
- call status
- SIP response code
- DIDWW call/event ID

Our backend should later expose a DIDWW webhook endpoint, for example:

```text
POST /webhooks/didww/call-events
```

Then it can link DIDWW CDRs to existing call records using:

- caller ID
- called number
- timestamp
- call duration
- SIP Call-ID if available
- ElevenLabs conversation metadata if it carries the SIP Call-ID

## Estimated Pricing Model

Known DIDWW number cost from `didww_did_and_toll_free_price_list.csv`:

```text
Nigeria Lagos DID prefix: 234-201
Included channels: 0
Setup price: $15.00
Monthly price: $15.00

Nigeria Lagos DID prefix: 234-201
Included channels: 2
Setup price: $16.00
Monthly price: $16.00
```

The DIDWW buy-number UI also showed:

```text
Incoming rate: $0.01/min
Billing option selected in screenshot: Pay per minute
```

This means the first-month pilot cost for the Lagos `234-201` number is roughly:

```text
$15 setup + $15 first monthly rental = $30 before usage
Inbound usage = $0.01/min
```

If choosing the 2 included channel option from the price list, the base number cost shown in the CSV is:

```text
$16 setup + $16 first monthly rental = $32 before usage
```

Outbound rates from `Customer_TierTerminationRates_FRM.xlsx`, effective July 2026:

```text
Local sheet:
Nigeria fixed/local/mobile prefixes: $0.078/min

International sheet:
Nigeria fixed: $0.1561/min
Nigeria Lagos/Abuja/fixed regions: $0.1479/min
Most Nigeria mobile prefixes: $0.1479/min
Some Glo/9Mobile prefixes: $0.1375/min
```

Because DIDWW support said Nigerian DIDs support local calling and calls to Nigeria should be passed locally once outbound trunking is enabled, the expected outbound pilot rate is likely the Local sheet rate:

```text
Expected Nigeria outbound local route: about $0.078/min
```

Still confirm inside the DIDWW outbound trunk setup which rate table applies to the active route.

ElevenLabs cost:

ElevenLabs uses subscription plans and credits. The public pricing page lists plans such as Free, Starter, Creator, Pro, Scale, Business, and Enterprise, with different monthly credit amounts. The exact conversational AI cost must be checked against the ElevenAgents usage/pricing inside the active account.

3CX cost:

Depends on the company existing 3CX license, trunk configuration, recording storage, and any provider charges for transferred calls.

### Example Monthly Estimate Formula

Use this formula rather than trusting one fixed number:

```text
monthly cost =
  DID monthly rental
+ inbound DIDWW minutes
+ outbound DIDWW minutes
+ DIDWW channel/capacity cost, if using dedicated channels
+ ElevenLabs AI usage
+ 3CX/human handoff call cost, if charged separately
+ recording/storage cost from ElevenLabs/3CX, if any
```

Example with placeholders:

```text
DID rental: $15/month
Inbound: inbound_minutes * $0.01
Outbound: outbound_minutes * DIDWW_outbound_rate_to_Nigeria
ElevenLabs: AI_minutes_or_credits_cost
3CX: existing license/trunk/recording cost
```

For a small pilot:

```text
Initial DIDWW cost: about $30
Then: $15/month + usage
```

Do not estimate production cost until we know:

- expected monthly inbound minutes
- expected monthly outbound minutes
- average call length
- expected handoff percentage
- ElevenLabs plan and usage cost
- DIDWW outbound Nigeria rate
- chosen inbound channel model

## Pilot Checklist

1. Buy the `+234-201` DID with a small/pilot channel configuration.
2. Enable DIDWW inbound SIP trunking.
3. Enable DIDWW outbound trunking.
4. Import the DIDWW number into ElevenLabs from SIP trunk.
5. Assign the ElevenLabs agent to the imported number.
6. Confirm inbound calls reach the agent.
7. Confirm caller ID appears in the ElevenLabs post-call webhook.
8. Confirm our backend saves the call record.
9. Test outbound calling from ElevenLabs through DIDWW to Nigerian mobile numbers.
10. Test across multiple Nigerian networks if possible: MTN, Airtel, Glo, 9mobile.
11. Configure ElevenLabs handoff to 3CX/officer.
12. Confirm 3CX receives the handoff.
13. Confirm where AI recording and human recording live.
14. Ask DIDWW support to enable Call Events/CDR webhook.
15. Build DIDWW CDR webhook ingestion in our backend.

## Source References

- DIDWW ElevenLabs integration: https://doc.didww.com/integrations/elevenlabs/index.html
- DIDWW docs home: https://doc.didww.com/
- ElevenLabs pricing: https://elevenlabs.io/pricing
- ElevenLabs SIP trunking: https://elevenlabs.io/docs/eleven-agents/phone-numbers/sip-trunking
