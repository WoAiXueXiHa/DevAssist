package api

import (
	"github.com/gin-gonic/gin"
	"os"
	"path/filepath"
	"sort"
	"strings"
)

var categories = map[string]string{"01": "接入", "02": "鉴权", "03": "错误码", "04": "回调", "05": "限流", "06": "计费", "07": "数据质量", "08": "FAQ"}

func docMeta(path string, content []byte) gin.H {
	name := filepath.Base(path)
	stem := strings.TrimSuffix(name, ".md")
	prefix := strings.SplitN(stem, "-", 2)[0]
	first := strings.SplitN(string(content), "\n", 2)[0]
	title := stem
	if strings.HasPrefix(first, "# ") {
		title = strings.TrimSpace(first[2:])
	}
	category, ok := categories[prefix]
	if !ok {
		category = "其它"
	}
	return gin.H{"id": prefix, "title": title, "category": category, "filename": name}
}
func (s *Server) docFiles() ([]string, error) {
	return filepath.Glob(filepath.Join(s.Config.KnowledgeDir, "*.md"))
}
func (s *Server) docs(c *gin.Context) {
	files, e := s.docFiles()
	if e != nil {
		fail(c, 500, "文档读取失败")
		return
	}
	sort.Strings(files)
	out := []gin.H{}
	for _, p := range files {
		b, e := os.ReadFile(p)
		if e != nil {
			fail(c, 500, "文档读取失败")
			return
		}
		out = append(out, docMeta(p, b))
	}
	c.JSON(200, gin.H{"documents": out})
}
func (s *Server) doc(c *gin.Context) {
	files, e := s.docFiles()
	if e != nil {
		fail(c, 500, "文档读取失败")
		return
	}
	id := c.Param("id")
	for _, p := range files {
		if strings.HasPrefix(filepath.Base(p), id+"-") {
			b, e := os.ReadFile(p)
			if e != nil {
				fail(c, 500, "文档读取失败")
				return
			}
			out := docMeta(p, b)
			out["content"] = string(b)
			c.JSON(200, out)
			return
		}
	}
	fail(c, 404, "文档不存在")
}
