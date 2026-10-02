/*
 * unoq-nmea2000 Stage 9.1 firmware — MCU-side CAN capture (Requirement 24.2-24.5)
 *
 * Board:      Arduino UNO Q (STM32U585 MCU side, Zephyr-based Arduino core)
 * Shield:     EVAL-ADM3055E-ARDZ (Analog Devices CN0401), MCP2518FD controller
 *             + ADM3055E isolated transceiver, classical CAN 2.0B mode.
 * Library:    ACAN2517 by Pierre Molinaro (github.com/pierremolinaro/acan2517)
 *             -- NOT ACAN2517FD: this project uses classical CAN only
 *             (Requirement 24.2), never CAN-FD.
 *
 * Receive-only safety (steering 01, Requirement 1, 24.3, 24.7-24.9):
 *   TWO INDEPENDENT silent/listen-only mechanisms are engaged, deliberately
 *   not just one:
 *     1. MCP2518FD controller's own ListenOnly operation mode (firmware/SPI
 *        configured, verified via currentOperationMode() -- Requirement
 *        24.3's "verifiable health metric").
 *     2. ADM3055E transceiver's HARDWARE silent-mode pin, driven by this
 *        sketch via Arduino Uno header pin D4 (requires the P5 shunt
 *        fitted on the shield -- Requirement 24.8's "shield D4 with the P5
 *        shunt fitted"). This is a SEPARATE control path from the MCP2518FD
 *        controller mode: the transceiver physically disallows driving the
 *        CAN bus regardless of what the controller is configured to do.
 *   D4 is driven HIGH (silent) in setup(), BEFORE Bridge.begin() and BEFORE
 *   can.begin(), and is never written LOW anywhere in this sketch -- there
 *   is no code path that can ever enable transmission (Requirement 1.4).
 *
 * CS pin (D10) is this shield's FACTORY-DEFAULT P2 jumper position (shunt
 * pins 1-2 per the CN0401/EVAL-ADM3055E-ARDZ datasheet). CONFIRMED against
 * the actual physical board on 2026-10-02 -- P2 is D10. If your board has
 * been rejumpered to D9/D8/D7, change MCP2518_CS below to match. Getting
 * this wrong means the sketch cannot talk to the controller at all
 * (begin() returns a nonzero error code) -- a safe failure mode, not a
 * silent one.
 *
 * INT pin (D2) requires the P7 shunt fitted (shield default).
 *
 * Bridge RPC contract exposed to the Linux (Python) side -- all REQUEST-
 * RESPONSE, since the MCU cannot push to Linux unprompted. Each handler
 * returns a plain String (see the ponytail note below the handlers for why
 * this is a line-based text encoding rather than a packed binary one):
 *   get_frame_batch()  -> newline-joined lines, oldest frame first, each
 *                         line "<id>,<ext 0|1>,<len>,<hex bytes>,<millis_ts>";
 *                         empty string if nothing pending (bounded batch
 *                         size per call -- see FRAME_BATCH_MAX).
 *   get_health()       -> one line: "total_frames,dropped_frames,
 *                         queue_depth,controller_mode,termination_off,
 *                         silent_pin_high,error_counters" (Requirement 24.5;
 *                         uptime is derived on the Linux side from Bridge
 *                         call latency/process start, not duplicated here).
 *   get_can_init_ok()  -> bool: whether can.begin() succeeded at setup().
 *
 * All Bridge-registered functions below are NON-BLOCKING (no delay()), per
 * the Bridge's own requirement that a slow handler times out the link.
 */

#include "Arduino_RouterBridge.h"
#include <ACAN2517.h>

// --- Pin configuration -- VERIFY against your physical shield's jumpers ---
static const byte MCP2518_CS = 10;   // P2 shunt pins 1-2 (shield factory default)
static const byte MCP2518_INT = 2;   // P7 shunt fitted (shield default)
static const byte TRANSCEIVER_SILENT_PIN = 4; // shield D4, requires P5 shunt fitted

// --- CAN controller instance ---
ACAN2517 can(MCP2518_CS, SPI, MCP2518_INT);

// --- Receive buffering: MCU polls the controller, Linux polls the MCU ---
static const uint8_t FRAME_BATCH_MAX = 16; // bounded so get_frame_batch() stays fast/non-blocking
struct BufferedFrame {
  uint32_t id;
  bool ext;
  uint8_t len;
  uint8_t data[8];
  uint32_t millis_ts;
};
static const uint16_t RING_SIZE = 256; // ~85ms of headroom at 100% CAN bus load worst case
static BufferedFrame ringBuffer[RING_SIZE];
static volatile uint16_t ringHead = 0; // next write position
static volatile uint16_t ringTail = 0; // next unread position
static volatile uint32_t totalFrameCount = 0;
static volatile uint32_t droppedFrameCount = 0; // ring overflow -- oldest-dropped, newest-kept
static bool canInitOk = false;

static uint16_t ringDepth() {
  return (uint16_t)((ringHead - ringTail + RING_SIZE) % RING_SIZE);
}

const char *operationModeName(ACAN2517::OperationMode mode) {
  switch (mode) {
    case ACAN2517::ListenOnly: return "ListenOnly";
    case ACAN2517::NormalFD: return "NormalFD"; // unused in this project; CAN-FD not enabled
    case ACAN2517::Normal20B: return "Normal20B"; // would indicate a config error if ever seen
    case ACAN2517::Sleep: return "Sleep";
    case ACAN2517::InternalLoopBack: return "InternalLoopBack";
    case ACAN2517::ExternalLoopBack: return "ExternalLoopBack";
    case ACAN2517::Configuration: return "Configuration";
    case ACAN2517::RestrictedOperation: return "RestrictedOperation";
    default: return "Unknown";
  }
}

// --- Bridge RPC handlers (Linux calls these; MCU never calls Linux) ---
//
// ponytail: the exact nested-array/struct return-type marshaling supported
// by Arduino_RPClite's generic provide_safe<F> template could not be
// verified from this environment (no compiler/flash cycle available to
// confirm a std::vector<struct> round-trips correctly over the Bridge).
// Rather than risk an incorrectly-framed binary return value, each frame
// is encoded as ONE plain-text line this sketch fully controls:
//   "<id>,<ext 0|1>,<len>,<hex bytes>,<millis_ts>"
// and a batch is newline-joined lines, matching the documented, confirmed-
// working String/const-char* return pattern from Arduino's own Bridge
// tutorial examples. Ceiling: string formatting costs more MCU cycles than
// binary packing would. Upgrade path: once a real Bridge round-trip is
// confirmed on the bench (Stage 9.2), measure whether this ever lags behind
// the CAN bus's actual max frame rate; if so, switch to a verified
// MsgPack-array-of-arrays return and drop this comment.
String get_frame_batch() {
  String out;
  uint16_t depth = ringDepth();
  if (depth > FRAME_BATCH_MAX) depth = FRAME_BATCH_MAX;

  for (uint16_t i = 0; i < depth; i++) {
    const BufferedFrame &f = ringBuffer[ringTail];
    if (i > 0) out += '\n';
    out += String(f.id);
    out += ',';
    out += f.ext ? '1' : '0';
    out += ',';
    out += String(f.len);
    out += ',';
    for (uint8_t b = 0; b < f.len; b++) {
      if (f.data[b] < 0x10) out += '0';
      out += String(f.data[b], HEX);
    }
    out += ',';
    out += String(f.millis_ts);
    ringTail = (uint16_t)((ringTail + 1) % RING_SIZE);
  }
  return out;
}

// Verifiable health metrics (Requirement 24.3, 24.4, 24.5), same plain-text
// line encoding as get_frame_batch() for the same verified-round-trip reason:
//   "total_frames,dropped_frames,queue_depth,controller_mode,termination_off,silent_pin_high,error_counters"
String get_health() {
  String out;
  out += String(totalFrameCount); out += ',';
  out += String(droppedFrameCount); out += ',';
  out += String(ringDepth()); out += ',';
  out += operationModeName(can.currentOperationMode()); out += ',';
  out += "true"; out += ','; // termination_off: hard-wired off, no runtime switch in this sketch
  out += (digitalRead(TRANSCEIVER_SILENT_PIN) == HIGH) ? "true" : "false"; out += ',';
  out += String(can.errorCounters());
  return out;
}

bool get_can_init_ok() {
  return canInitOk;
}

void setup() {
  // --- Receive-only safety FIRST, before anything else touches the bus ---
  // (Requirement 1.6: non-driving through startup; this line executes before
  // SPI/Bridge/CAN init, so the transceiver is silenced for the entire
  // remainder of setup() too, not just steady-state operation.)
  pinMode(TRANSCEIVER_SILENT_PIN, OUTPUT);
  digitalWrite(TRANSCEIVER_SILENT_PIN, HIGH); // HIGH = transceiver silent mode (requires P5 shunt)

  Bridge.begin();
  Monitor.begin();
  Monitor.println("unoq-nmea2000 firmware starting (receive-only, silent mode engaged)");

  SPI.begin();

  // 20 MHz oscillator is the MCP2518FD shield's typical crystal; VERIFY
  // against your board's actual oscillator if begin() reports a bit-rate
  // error (Requirement 24.2: must land on exactly 250 kbit/s).
  ACAN2517Settings settings(ACAN2517Settings::OSC_20MHz, 250UL * 1000UL);
  settings.mRequestedMode = ACAN2517Settings::ListenOnly; // Requirement 1.2, 24.3

  const uint32_t errorCode = can.begin(settings, [] { can.isr(); });
  canInitOk = (errorCode == 0);

  if (canInitOk) {
    Monitor.print("CAN controller init OK, mode=");
    Monitor.println(operationModeName(can.currentOperationMode()));
  } else {
    // Requirement 1.3: if silent/listen-only cannot be configured, the
    // Transmit_Path stays disabled (the transceiver is ALREADY silenced via
    // D4 above, independent of this failure) and we report the fault rather
    // than retry into an unverified state.
    Monitor.print("CAN controller init FAILED, error=0x");
    Monitor.println(errorCode, HEX);
  }

  Bridge.provide_safe("get_frame_batch", get_frame_batch);
  Bridge.provide_safe("get_health", get_health);
  Bridge.provide_safe("get_can_init_ok", get_can_init_ok);
  Monitor.println("Bridge ready");
}

void loop() {
  // Drain the controller's receive FIFO into our ring buffer as fast as
  // possible; Linux polls get_frame_batch() separately via Bridge. No
  // delay() anywhere in this path (Bridge handlers must stay non-blocking).
  CANMessage message;
  while (can.receive(message)) {
    uint16_t nextHead = (uint16_t)((ringHead + 1) % RING_SIZE);
    if (nextHead == ringTail) {
      // Ring full: drop the OLDEST buffered frame to make room for the
      // newest (Requirement 2.5-style buffer-to-capacity behavior, scoped
      // to this MCU-side ring) rather than drop the newly-received frame
      // silently -- either way is a drop, but counting it here keeps
      // get_health()'s dropped_frames accurate.
      ringTail = (uint16_t)((ringTail + 1) % RING_SIZE);
      droppedFrameCount++;
    }
    BufferedFrame &f = ringBuffer[ringHead];
    f.id = message.id;
    f.ext = message.ext;
    f.len = message.len;
    memcpy(f.data, message.data_s8, message.len);
    f.millis_ts = millis();
    ringHead = nextHead;
    totalFrameCount++;
  }
}
