import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
from PIL import Image

from engine.workflow import collect, draft, group_numbers


class InterviewProgressTests(unittest.TestCase):
    def test_group_number_pointer_and_saved_draft_use_same_subject(self):
        photo = Image.new("RGB",(4,3))
        groups = {"subject":np.ones((3,4),bool), "background":np.zeros((3,4),bool)}
        numbers = group_numbers({"groups":[{"id":1,"key":"subject"},{"id":3,"key":"background"}]})
        rows = [dict(label=k,name=k) for k in ("floor","car","road")]
        with tempfile.TemporaryDirectory() as tmp:
            file=Path(tmp)/"answers.json"
            file.write_text(json.dumps(draft(photo)))
            results=[]
            for answer in ("pick","1"):
                replies=["floor=road","",answer,"high_speed","detail","car","wet"]+["strong"]*13
                selected=[]
                with patch("sys.stdin.isatty",return_value=True),patch("builtins.input",side_effect=replies),patch("builtins.print"):
                    results.append(collect(photo,rows,file,None,None,groups,numbers=numbers,
                                           pick=lambda:numbers[1],highlight=lambda key,merge:selected.append(key)))
                self.assertEqual(selected,["group:subject"])
            self.assertEqual(results[0],results[1])
            self.assertEqual(results[0]["merge_into"],{"floor":"road"})
            self.assertEqual(results[0]["weather"]["大雪"],"strong")

    def test_resume_interrupted_detailed_answers(self):
        photo = Image.new("RGB",(4,3))
        rows = [dict(label="car",name="car")]
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/"draft.json"
            def save(plan):
                path.write_text(json.dumps(plan))
            with patch("sys.stdin.isatty",return_value=True),patch("builtins.print"):
                with patch("builtins.input",side_effect=["","car","stationary","detail","","strong",EOFError()]),self.assertRaises(EOFError):
                    collect(photo,rows,None,None,None,checkpoint=save)
                with patch("builtins.input",side_effect=["subtle"]*12) as prompts:
                    plan=collect(photo,rows,path,None,None)
                self.assertEqual(prompts.call_count,12)
                self.assertEqual(plan["weather"]["晴朗"],"strong")
                self.assertEqual(plan["weather"]["暴雪"],"subtle")
