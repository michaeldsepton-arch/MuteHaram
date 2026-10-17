package com.muteharam.player

import org.json.JSONObject

/** What the PC offers: the picture address and how to play it (see pc/muteharam_pc/play.py run_tablet). */
data class SessionInfo(
    val id: Long,
    val title: String,
    val video: String,
    val kind: String,                    // "hls" | "dash" | "progressive"
    val headers: Map<String, String>,
    val subs: List<Pair<String, String>>, // url to language
    val startSeconds: Double,
    val mode: String = "music",         // "music" = standard music removal, "bandit" = better film model, "speech" = speech only
) {
    companion object {
        fun parse(json: String): SessionInfo {
            val o = JSONObject(json)
            val h = o.optJSONObject("headers")
            val headers = HashMap<String, String>()
            if (h != null) for (k in h.keys()) headers[k] = h.optString(k)
            val subs = ArrayList<Pair<String, String>>()
            val sa = o.optJSONArray("subs")
            if (sa != null) for (i in 0 until sa.length()) {
                val e = sa.getJSONObject(i)
                val u = e.optString("url")
                if (u.startsWith("http")) subs.add(u to e.optString("lang"))
            }
            return SessionInfo(
                id = o.getLong("id"),
                title = o.optString("title"),
                video = o.getString("video"),
                kind = o.optString("kind", "progressive"),
                headers = headers,
                subs = subs,
                startSeconds = o.optDouble("start", 0.0).let { if (it.isNaN() || it < 0) 0.0 else it },
                mode = o.optString("mode").let { if (it == "speech" || it == "bandit") it else "music" },
            )
        }

        fun subtitleMime(url: String): String {
            val u = url.lowercase().substringBefore('?')
            return when {
                u.endsWith(".srt") -> "application/x-subrip"
                u.endsWith(".ass") || u.endsWith(".ssa") -> "text/x-ssa"
                u.endsWith(".ttml") || u.endsWith(".xml") -> "application/ttml+xml"
                else -> "text/vtt"
            }
        }
    }
}
