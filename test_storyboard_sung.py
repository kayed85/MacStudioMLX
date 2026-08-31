"""SUNG — the third honest state of the speech law.

The storyboard's dialogue machinery has always known SPOKEN and SILENT, and
singing fell through the crack in both directions: a "he sings" shot with no
lyric escaped the board-level law entirely (`_IMPLIES_SPEECH_RE` had no sing
verb), and a sung line WITH words was paced against the 2.4 w/s speaking
budget — nearly double what a sung mantra actually carries, so a correctly
written lyric shot either got refused as overstuffed at honest durations or
approved with double the words it could sing.

The rate is measured, not styled: the owner-graded AVRELIVS "Amor fati"
joint takes (2026-08-30) delivered 6 sung words in a 5.04 s shot (needs the
budget to allow >= 1.49 w/s after the 1 s settle) and 12 words in 10.04 s
(>= 1.33). SPEECH_WORDS_PER_SEC_SUNG = 1.5 admits both.

Also pinned: the bird guard. "birds singing at dawn" is scenery, not a mouth
this law owns — the same false-positive caution `_IMPLIES_SPEECH_RE` already
carries for "brief(ing room)".
"""

import unittest

from storyboard import (
    SPEECH_WORDS_PER_SEC,
    SPEECH_WORDS_PER_SEC_SUNG,
    is_sung,
    shot_pacing_problem,
    shot_speech_problem,
    speech_fit_frames,
)
from storyboard_planner import (
    _neutralise_speech,
    _speech_violations,
)


class SungImpliesSpeech(unittest.TestCase):
    """A singing mouth with no lyric is the wordless-explains bug in a robe."""

    def test_sings_with_no_words_is_blocked(self):
        for prompt in (
            "A marble bust of a hooded man sings directly to the camera",
            "She is chanting under the arch, arms raised",
        ):
            with self.subTest(prompt=prompt):
                self.assertIsNotNone(shot_speech_problem(prompt))

    def test_sings_with_the_lyric_is_legal(self):
        self.assertIsNone(shot_speech_problem(
            "A marble bust sings 'Amor fati. Love your fate.' directly to camera"))

    def test_birdsong_is_scenery_not_speech(self):
        for prompt in (
            "Dawn light over the marsh, birds singing in the reeds",
            "A single bird singing somewhere above the courtyard",
        ):
            with self.subTest(prompt=prompt):
                self.assertIsNone(shot_speech_problem(prompt))


class SungDetection(unittest.TestCase):
    def test_positive_forms(self):
        for p in ("he sings 'Love your fate.'",
                  "chanting 'Amor fati.' slowly",
                  "the sung line 'Love your fate.' lands on the downbeat"):
            with self.subTest(p=p):
                self.assertTrue(is_sung(p))

    def test_negative_forms(self):
        for p in ("he says 'Love your fate.' slowly",
                  "birds singing over the field as he watches",
                  "a single tone rings out"):
            with self.subTest(p=p):
                self.assertFalse(is_sung(p))


class SungPacing(unittest.TestCase):
    """The measured evidence, admitted; the speaking budget's blind spot, closed."""

    def test_rate_sits_between_the_evidence_and_speech(self):
        self.assertGreaterEqual(SPEECH_WORDS_PER_SEC_SUNG, 1.49,
                                "refuses the owner-graded 6-words-in-5.04s take")
        self.assertLess(SPEECH_WORDS_PER_SEC_SUNG, SPEECH_WORDS_PER_SEC)

    def test_the_delivered_takes_fit(self):
        # 121f take: 6 words in 5.04 s. 241f take: 12 words in 10.04 s.
        self.assertIsNone(shot_pacing_problem(
            "He sings 'Amor fati. Love your fate.' like a mantra.", 5.04))
        self.assertIsNone(shot_pacing_problem(
            "He sings 'Amor fati. Love your fate. Amor fati. Love your fate.' "
            "twice, slowly.", 10.04))

    def test_a_spoken_sized_line_is_refused_when_sung(self):
        # 12 words in 6 s: fine spoken (allowed 12.0), overstuffed sung (7.5).
        line = "'One two three four five six seven eight nine ten eleven twelve.'"
        self.assertIsNone(shot_pacing_problem("He says " + line, 6.0))
        problem = shot_pacing_problem("He sings " + line, 6.0)
        self.assertIsNotNone(problem)
        self.assertIn("singing tempo", problem)

    def test_fit_frames_reproduces_the_graded_take(self):
        # 6 sung words -> exactly the 121-frame shot that delivered.
        self.assertEqual(speech_fit_frames(6, sung=True), 121)


class PlannerSungLaw(unittest.TestCase):
    """Planner side: same blind spot closed, and neutralisation stays honest."""

    def test_wordless_sings_is_a_violation(self):
        problems = _speech_violations("She sings to the empty hall", "")
        self.assertTrue(any("sing" in p.lower() for p in problems))

    def test_sung_line_in_the_tag_is_no_violation(self):
        self.assertEqual(_speech_violations(
            "She sings <d>[English] Love your fate.</d> to the empty hall", ""), [])

    def test_birdsong_is_no_violation(self):
        self.assertEqual(_speech_violations(
            "He kneels in the grass, birds singing above him", ""), [])

    def test_neutralised_singing_stops_claiming_a_melody_from_a_mouth(self):
        desc, _, notes = _neutralise_speech("She sings to the empty hall", "")
        self.assertNotIn("sings", desc)
        self.assertIn("sways", desc)
        self.assertTrue(any("silenced" in n for n in notes))


if __name__ == "__main__":
    unittest.main()
