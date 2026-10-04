package model

import (
	"encoding/json"
	"testing"
	"time"
)

func TestJSONAndPythonTime(t *testing.T) {
	for _, input := range []string{`null`, `[]`, `{}`, `false`, `0`, `{"list":[1,"中"]}`} {
		var j JSON
		if e := j.Scan([]byte(input)); e != nil {
			t.Fatal(e)
		}
		b, e := json.Marshal(j)
		if e != nil || string(b) != input {
			t.Fatal(string(b), e)
		}
		sql, e := j.Value()
		if e != nil || sql != input {
			t.Fatal(sql, e)
		}
	}
	if ISO(time.Date(2026, 6, 15, 1, 2, 3, 123000, time.UTC)) != "2026-06-15T01:02:03.000123" {
		t.Fatal("微秒格式错误")
	}
	if ISO(time.Date(2026, 6, 15, 1, 2, 3, 0, time.UTC)) != "2026-06-15T01:02:03" {
		t.Fatal("秒格式错误")
	}
}
