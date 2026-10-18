package com.muteharam.player

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class SessionInfoTest {
    @Test
    fun parsesWhatThePcSends() {
        val s = SessionInfo.parse(
            """{"id": 1792270494238, "title": "Film", "video": "https://cdn.example/x/playlist?t=1", "kind": "hls",
               "headers": {"Referer": "https://site.example/", "User-Agent": "UA"},
               "subs": [{"url": "https://s.example/en.vtt", "lang": "en"}, {"url": "ftp://bad", "lang": "x"}],
               "start": 12.5, "duration": 7714.0}"""
        )
        assertEquals(1792270494238L, s.id)
        assertEquals("hls", s.kind)
        assertEquals("https://site.example/", s.headers["Referer"])
        assertEquals(1, s.subs.size)
        assertEquals("en", s.subs[0].second)
        assertEquals(12.5, s.startSeconds, 1e-9)
    }

    @Test
    fun missingOptionalFieldsAreFine() {
        val s = SessionInfo.parse("""{"id": 5, "video": "https://v.example/a.mp4"}""")
        assertEquals("progressive", s.kind)
        assertTrue(s.headers.isEmpty() && s.subs.isEmpty())
        assertEquals(0.0, s.startSeconds, 0.0)
        assertEquals("music", s.mode)
    }

    @Test
    fun ageIsReadAndDefaultsToZero() {
        assertEquals(125L, SessionInfo.parse("""{"id": 1, "video": "https://v/a.mp4", "age_s": 125}""").ageSeconds)
        assertEquals(0L, SessionInfo.parse("""{"id": 1, "video": "https://v/a.mp4"}""").ageSeconds)
    }

    @Test
    fun modeIsReadAndDefaultsToMusic() {
        assertEquals("speech", SessionInfo.parse("""{"id": 1, "video": "https://v/a.mp4", "mode": "speech"}""").mode)
        assertEquals("bandit", SessionInfo.parse("""{"id": 1, "video": "https://v/a.mp4", "mode": "bandit"}""").mode)
        assertEquals("sam", SessionInfo.parse("""{"id": 1, "video": "https://v/a.mp4", "mode": "sam"}""").mode)
        assertEquals("music", SessionInfo.parse("""{"id": 1, "video": "https://v/a.mp4", "mode": "nonsense"}""").mode)
    }

    @Test
    fun subtitleTypesAreRecognised() {
        assertEquals("text/vtt", SessionInfo.subtitleMime("https://a/b.vtt?x=1"))
        assertEquals("application/x-subrip", SessionInfo.subtitleMime("https://a/b.SRT"))
    }
}
