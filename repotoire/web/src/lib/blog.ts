import fs from "fs";
import path from "path";
import matter from "gray-matter";

const BLOG_DIR = path.join(process.cwd(), "src", "content", "blog");

export type PostFrontmatter = {
  title: string;
  description: string;
  date: string;
  author: string;
  tags: string[];
  image?: string;
};

export type PostMeta = PostFrontmatter & {
  slug: string;
  readingTime: number;
};

export function getReadingTime(content: string): number {
  const words = content.trim().split(/\s+/).length;
  return Math.ceil(words / 200);
}

export function getAllPosts(): PostMeta[] {
  if (!fs.existsSync(BLOG_DIR)) return [];
  const files = fs.readdirSync(BLOG_DIR).filter((f) => f.endsWith(".mdx"));

  const posts = files.map((filename) => {
    const slug = filename.replace(/\.mdx$/, "");
    const raw = fs.readFileSync(path.join(BLOG_DIR, filename), "utf-8");
    const { data, content } = matter(raw);
    const frontmatter = data as PostFrontmatter;

    return {
      ...frontmatter,
      slug,
      readingTime: getReadingTime(content),
    };
  });

  return posts.sort(
    (a, b) => new Date(b.date).getTime() - new Date(a.date).getTime()
  );
}

export function getPostBySlug(slug: string) {
  if (!/^[a-z0-9-]+$/.test(slug)) throw new Error("Invalid slug");
  const filePath = path.join(BLOG_DIR, `${slug}.mdx`);
  const raw = fs.readFileSync(filePath, "utf-8");
  const { data, content } = matter(raw);
  const frontmatter = data as PostFrontmatter;

  return {
    frontmatter,
    content,
    readingTime: getReadingTime(content),
  };
}

export function getRelatedPosts(currentSlug: string, limit = 3): PostMeta[] {
  const allPosts = getAllPosts();
  const current = allPosts.find((p) => p.slug === currentSlug);
  if (!current) return [];

  const scored = allPosts
    .filter((p) => p.slug !== currentSlug)
    .map((post) => {
      const shared = post.tags.filter((t) => current.tags.includes(t)).length;
      return { post, shared };
    })
    .filter((s) => s.shared > 0)
    .sort((a, b) => b.shared - a.shared);

  return scored.slice(0, limit).map((s) => s.post);
}
