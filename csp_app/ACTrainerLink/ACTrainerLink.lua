-- AC Trainer Link: records every other car's laps so the coach can learn from faster drivers.
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

function windowMain(dt)
  ui.text('AC Trainer Link')
  ui.text(status)
end
