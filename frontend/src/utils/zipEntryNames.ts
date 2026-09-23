/**
 * 归一化条目名不规范的 OOXML 包（zip 条目名用反斜杠）。
 *
 * .docx / .xlsx 本身就是 zip 包，内部部件名按 OOXML 规范必须用正斜杠
 * （`word/document.xml`）。WPS 等第三方 Office 存盘时可能写成反斜杠
 * （`word\document.xml`），此时浏览器端的 mammoth / SheetJS 按规范名查不到
 * 部件，预览直接报「无法解析 Word 文件」—— 与后端 python-docx 在 Linux 上
 * 的失败同源（详见 docs/development/debug-log.md BUG-20260923-001）。
 *
 * 做法与后端一致，只改条目名、不重新压缩：反斜杠与正斜杠都是单字节，就地
 * 替换后偏移、CRC、压缩数据全部原样保持。规范包返回同一个缓冲区，零开销。
 */

// zip 结构里的定长字段偏移（小端）。局部头 30 字节后是条目名，中央目录条目
// 再多 16 字节（磁盘号/内部属性/外部属性/局部头偏移），名字从第 46 字节开始。
const EOCD_SIGNATURE = 0x06054b50
const CENTRAL_SIGNATURE = 0x02014b50
const EOCD_MIN_SIZE = 22
const MAX_COMMENT = 65535 // zip 尾注释上限：EOCD 只可能落在最后 22 + 65535 字节内
const LOCAL_NAME_OFFSET = 30
const CENTRAL_NAME_OFFSET = 46

const BACKSLASH = 0x5c
const SLASH = 0x2f

/** 中央目录条目与局部头里各自那段条目名的位置。 */
interface NameSite {
  at: number
  length: number
}

/**
 * 扫中央目录，收集所有条目名的位置，并判断是否存在反斜杠。
 *
 * 读不出结构（文件过小、不是 zip、损坏、zip64）时返回 null，让调用方原样把
 * 缓冲区交给解析器 —— 报错留给原本的解析路径，本函数不改变任何错误行为。
 */
function findNameSites(bytes: Uint8Array): { sites: NameSite[]; hasBackslash: boolean } | null {
  const total = bytes.length
  if (total < EOCD_MIN_SIZE) return null
  const view = new DataView(bytes.buffer, bytes.byteOffset, total)

  let eocd = -1
  const lowest = Math.max(0, total - EOCD_MIN_SIZE - MAX_COMMENT)
  for (let i = total - EOCD_MIN_SIZE; i >= lowest; i--) {
    if (view.getUint32(i, true) === EOCD_SIGNATURE) {
      eocd = i
      break
    }
  }
  if (eocd < 0) return null

  const centralSize = view.getUint32(eocd + 12, true)
  const centralOffset = view.getUint32(eocd + 16, true)
  if (centralOffset + centralSize > total) return null

  const sites: NameSite[] = []
  let hasBackslash = false
  let pos = centralOffset
  const end = centralOffset + centralSize
  while (pos + CENTRAL_NAME_OFFSET <= end && view.getUint32(pos, true) === CENTRAL_SIGNATURE) {
    const nameLength = view.getUint16(pos + 28, true)
    const extraLength = view.getUint16(pos + 30, true)
    const commentLength = view.getUint16(pos + 32, true)
    const nameAt = pos + CENTRAL_NAME_OFFSET
    if (nameAt + nameLength > total) return null

    sites.push({ at: nameAt, length: nameLength })
    for (let i = 0; i < nameLength; i++) {
      if (bytes[nameAt + i] === BACKSLASH) hasBackslash = true
    }

    // 同一份条目在局部头里还有一段名字，长度一致才改（不一致说明结构异常）
    const localOffset = view.getUint32(pos + 42, true)
    if (
      localOffset + LOCAL_NAME_OFFSET + 2 <= total &&
      view.getUint16(localOffset + 26, true) === nameLength
    ) {
      sites.push({ at: localOffset + LOCAL_NAME_OFFSET, length: nameLength })
    }

    pos += CENTRAL_NAME_OFFSET + nameLength + extraLength + commentLength
  }

  return { sites, hasBackslash }
}

/**
 * 条目名含反斜杠时返回一份改成正斜杠的副本；规范包原样返回入参。
 *
 * 入参不被修改 —— 副本只在不规范时才产生，规范文件（绝大多数）走的是直接
 * 返回同一个 ArrayBuffer 的路径。
 */
export function normalizeZipEntryNames(buffer: ArrayBuffer): ArrayBuffer {
  const found = findNameSites(new Uint8Array(buffer))
  if (!found || !found.hasBackslash) return buffer

  const patched = new Uint8Array(buffer.slice(0))
  for (const site of found.sites) {
    for (let i = 0; i < site.length; i++) {
      const at = site.at + i
      if (patched[at] === BACKSLASH) patched[at] = SLASH
    }
  }
  return patched.buffer
}
