-- AC Trainer Link: records every other car's laps so the coach can learn from faster drivers,
-- and draws the coach's braking markers and your best line on the track (see the end of the file).
--
-- Shared memory and UDP telemetry only describe *your* car; CSP's Lua API can see all of
-- them. Each completed lap of another car is appended as one JSON line to
--   Documents/Assetto Corsa/actrainer/rival_laps.jsonl
-- rows: [lap time s, spline pos, speed km/h, gas, brake, gear, rpm, x, z] every ~2 m.
-- Online, other cars' pedals may not be replicated (they read 0); the coach then works
-- from the speed trace alone.

local OUT_DIR = ac.getFolder(ac.FolderID.ACDocuments) .. '/actrainer'
local OUT_FILE = OUT_DIR .. '/rival_laps.jsonl'
local STEP_M = 2
local MAX_BYTES = 50 * 1024 * 1024

local buffers = {}
local written = 0
local status = 'waiting for laps'

pcall(io.createDir, OUT_DIR)

local function safe(fn, fallback)
  local ok, value = pcall(fn)
  if ok and value ~= nil then return value end
  return fallback
end

local function jsonString(s)
  s = tostring(s or '')
  s = s:gsub('\\', '\\\\'):gsub('"', '\\"'):gsub('[%c]', ' ')
  return '"' .. s .. '"'
end

local function rotateIfLarge()
  local f = io.open(OUT_FILE, 'r')
  if not f then return end
  local size = f:seek('end')
  f:close()
  if size and size > MAX_BYTES then
    os.remove(OUT_FILE .. '.old')
    os.rename(OUT_FILE, OUT_FILE .. '.old')
  end
end

local function writeLap(index, car, buf)
  local lapMs = car.previousLapTimeMs
  if not lapMs or lapMs <= 0 then return end
  local parts = {
    '{"v":1',
    ',"ts":', tostring(os.time()),
    ',"track":', jsonString(safe(ac.getTrackID, '')),
    ',"layout":', jsonString(safe(ac.getTrackLayout, '')),
    ',"model":', jsonString(safe(function() return ac.getCarID(index) end, '')),
    ',"driver":', jsonString(safe(function() return ac.getDriverName(index) end, 'car ' .. index)),
    ',"ai":', tostring(safe(function() return car.isAIControlled end, false) == true),
    ',"lap_ms":', tostring(lapMs),
    ',"rows":[', table.concat(buf.rows, ','), ']}\n',
  }
  rotateIfLarge()
  local f = io.open(OUT_FILE, 'a')
  if not f then
    status = 'cannot write ' .. OUT_FILE
    return
  end
  f:write(table.concat(parts))
  f:close()
  written = written + 1
  status = string.format('%d laps recorded, last %s %.3fs', written,
    safe(function() return ac.getDriverName(index) end, '?'), lapMs / 1000)
end

local function newBuffer(car)
  local s = car.splinePosition
  return { lap = car.lapCount, rows = {}, last = -1,
           atLine = car.lapTimeMs < 1500 and (s < 0.05 or s > 0.95), invalid = false }
end

function script.update(dt)
  local sim = ac.getSim()
  local trackLen = sim.trackLengthM > 0 and sim.trackLengthM or 5000
  for i = 1, sim.carsCount - 1 do   -- car 0 is you
    local car = ac.getCar(i)
    if car and car.isConnected ~= false then
      local buf = buffers[i]
      if not buf or car.lapCount ~= buf.lap then
        if buf and car.lapCount == buf.lap + 1 and buf.atLine and not buf.invalid and #buf.rows > 100 then
          writeLap(i, car, buf)
        end
        buffers[i] = newBuffer(car)
        buf = buffers[i]
      end
      if car.isInPitlane or (car.wheelsOutside or 0) > 2 then buf.invalid = true end
      local s = car.splinePosition
      if buf.last < 0 or math.abs(s - buf.last) * trackLen >= STEP_M then
        buf.last = s
        buf.rows[#buf.rows + 1] = string.format('[%.3f,%.6f,%.2f,%.3f,%.3f,%d,%.0f,%.1f,%.1f]',
          car.lapTimeMs / 1000, s, car.speedKmh, car.gas or 0, car.brake or 0, car.gear or 0,
          car.rpm or 0, car.position.x, car.position.z)
      end
    end
  end
end

-- Overlay: the coach writes overlay.json after every lap (actrainer/overlay.py) with your best
-- line and the braking / throttle markers of the corners it is working on; we draw them on
-- the track. Positions come as world x/z, the height from a raycast onto the track surface.

local OVERLAY_FILE = OUT_DIR .. '/overlay.json'
local MODES = { 'off', 'focus corners', 'full lap' }
local AHEAD_M = 350        -- full lap mode: draw this far ahead of the car
local LINE_HALF_WIDTH = 0.25
local BAR_HALF_WIDTH = 5
local BAR_HALF_DEPTH = 0.5
local PHASE_COLORS = {
  [0] = rgbm(1, 0.15, 0.1, 0.75),  -- braking
  [1] = rgbm(1, 1, 1, 0.45),       -- coasting
  [2] = rgbm(0.2, 1, 0.3, 0.6),    -- throttle
}
local BRAKE_COLOR = rgbm(1, 0.1, 0.05, 0.85)
local BRAKE_DIM = rgbm(1, 0.4, 0.3, 0.35)
local GAS_COLOR = rgbm(0.2, 1, 0.3, 0.85)
local APEX_COLOR = rgbm(1, 0.85, 0.1, 0.8)
local UP = vec3(0, 1, 0)

local settings = ac.storage({ overlayMode = 3 })   -- index into MODES
local toggle = ac.ControlButton('ACTrainerLink/Cycle overlay')
local overlay, overlayTime, overlayCheck = nil, -2, 0
local overlayStatus = 'no overlay yet: drive a lap with the coach running'
local ground = {}       -- line index -> vec3 on the track surface, filled as needed
local carIdx = nil

local rayFrom, rayDown, rayHit = vec3(), vec3(0, -1, 0), vec3()
local function groundAt(x, z, refY)
  rayFrom:set(x, refY + 50, z)
  if physics.raycastTrack(rayFrom, rayDown, 150, rayHit) < 0 then return nil end
  return vec3(rayHit.x, rayHit.y + 0.04, rayHit.z)
end

local function loadOverlay()
  local t = io.lastWriteTime(OVERLAY_FILE)
  if t == overlayTime then return end
  overlayTime = t
  if t < 0 then return end
  local ok, data = pcall(function() return JSON.parse(io.load(OVERLAY_FILE)) end)
  if not ok or type(data) ~= 'table' or type(data.line) ~= 'table' or #data.line < 10 then
    overlay, overlayStatus = nil, 'cannot read overlay.json'
    return
  end
  local car, track = safe(function() return ac.getCarID(0) end, ''), safe(ac.getTrackID, '')
  if data.car ~= car or data.track ~= track or (data.layout or '') ~= safe(ac.getTrackLayout, '') then
    overlay, overlayStatus = nil, 'overlay is for ' .. tostring(data.car) .. ' @ ' .. tostring(data.track)
    return
  end
  overlay, ground, carIdx = data, {}, nil
  local focus = {}
  for _, c in ipairs(data.corners) do
    if c.focus ~= '' then focus[#focus + 1] = c.name end
  end
  overlayStatus = #focus > 0 and ('working on ' .. table.concat(focus, ', ')) or 'no focus corners right now'
end

local function linePoint(i, refY)
  local p = ground[i]
  if p == nil then
    local q = overlay.line[i]
    p = groundAt(q[1], q[2], refY)
    ground[i] = p   -- a miss stays nil and is retried, e.g. once the car is closer
  end
  return p
end

local function lineIndex(d)
  local n = #overlay.line
  return math.floor((d % overlay.length) / overlay.step) % n + 1
end

local function nearestIndex(pos)
  local line, n = overlay.line, #overlay.line
  local from, to = 1, n
  if carIdx then from, to = carIdx - 20, carIdx + 40 end
  local best, bestD2 = nil, 30 * 30
  for k = from, to do
    local i = (k - 1) % n + 1
    local dx, dz = line[i][1] - pos.x, line[i][2] - pos.z
    local d2 = dx * dx + dz * dz
    if d2 < bestD2 then best, bestD2 = i, d2 end
  end
  if not best and carIdx then   -- lost it (reset, pits): search the whole lap
    carIdx = nil
    return nearestIndex(pos)
  end
  carIdx = best
  return best
end

local side = vec3()
local function drawLine(from, count, refY)
  local n = #overlay.line
  local prev = linePoint((from - 1) % n + 1, refY)
  for k = from + 1, from + count do
    local i = (k - 1) % n + 1
    local p = linePoint(i, refY)
    if prev and p then
      side:set(p.z - prev.z, 0, prev.x - p.x):normalize():scale(LINE_HALF_WIDTH)
      render.quad(prev - side, prev + side, p + side, p - side, PHASE_COLORS[overlay.line[i][3]], nil, true)
    end
    prev = p
  end
end

-- a bar across the track at a marker {x, z, heading x, heading z}, with a label above it
local function drawBar(m, color, label, refY)
  if not m.l then
    local sx, sz = m[4] * BAR_HALF_WIDTH, -m[3] * BAR_HALF_WIDTH
    m.l, m.r = groundAt(m[1] - sx, m[2] - sz, refY), groundAt(m[1] + sx, m[2] + sz, refY)
    if not m.l or not m.r then m.l, m.r = nil, nil return end
    m.f = vec3(m[3], 0, m[4]):scale(BAR_HALF_DEPTH)
  end
  render.quad(m.l - m.f, m.r - m.f, m.r + m.f, m.l + m.f, color, nil, true)
  if label then render.debugText((m.l + m.r):scale(0.5) + UP * 1.5, label, color, 1.2) end
end

local function drawApex(m, refY)
  if m.g == nil then m.g = groundAt(m[1], m[2], refY) end
  if m.g then render.circle(m.g, UP, 0.6, APEX_COLOR, nil, true) end
end

function script.draw3D()
  local mode = settings.overlayMode
  if not overlay or mode == 1 then return end
  local car = ac.getCar(0)
  if not car then return end
  local i = nearestIndex(car.position)
  if not i then return end
  local refY, full = car.position.y, mode == 3
  local carD = (i - 1) * overlay.step
  render.setBlendMode(render.BlendMode.AlphaBlend)
  render.setDepthMode(render.DepthMode.ReadOnlyLessEqual)
  if full then drawLine(i, math.floor(AHEAD_M / overlay.step), refY) end
  for _, c in ipairs(overlay.corners) do
    local focus = c.focus ~= ''
    local ahead = (c.from - carD) % overlay.length
    local inside = (carD - c.from) % overlay.length < (c.to - c.from) % overlay.length
    if (focus or full) and (ahead < AHEAD_M or inside) then
      if not full then
        local a, b = lineIndex(c.from), lineIndex(c.to)
        drawLine(a, (b - a) % #overlay.line, refY)
      end
      if c.brake and c.show_brake then
        drawBar(c.brake, focus and BRAKE_COLOR or BRAKE_DIM, focus and c.brake_label or nil, refY)
      end
      if focus and c.gas and c.show_gas then drawBar(c.gas, GAS_COLOR, c.gas_label, refY) end
      if focus then drawApex(c.apex, refY) end
    end
  end
end
draw3D = script.draw3D

local function cycleMode()
  settings.overlayMode = settings.overlayMode % #MODES + 1
  ac.setMessage('AC Trainer overlay', MODES[settings.overlayMode])
end

local baseUpdate = script.update
function script.update(dt)
  baseUpdate(dt)
  overlayCheck = overlayCheck - dt
  if overlayCheck <= 0 then
    overlayCheck = 1
    loadOverlay()
  end
  if toggle:pressed() then cycleMode() end
end

function windowMain(dt)
  ui.text('AC Trainer Link')
  ui.text(status)
  ui.textWrapped('Overlay: ' .. overlayStatus)
  if ui.button('Overlay: ' .. MODES[settings.overlayMode]) then cycleMode() end
  ui.text('Key to cycle the overlay:')
  toggle:control(vec2(180, 0))
end
