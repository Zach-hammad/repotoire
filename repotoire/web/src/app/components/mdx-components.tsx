import type { MDXComponents } from "mdx/types";

export const mdxComponents: MDXComponents = {
  h2: (props) => (
    <h2
      className="text-xl font-display font-bold text-foreground mt-10 mb-3"
      {...props}
    />
  ),
  h3: (props) => (
    <h3
      className="text-lg font-display font-semibold text-foreground mt-8 mb-3"
      {...props}
    />
  ),
  p: (props) => (
    <p className="text-sm text-muted-foreground leading-relaxed mb-4" {...props} />
  ),
  a: (props) => (
    <a
      className="text-primary hover:underline transition-colors"
      {...props}
    />
  ),
  ul: ({ children, ...props }) => (
    <ul className="text-sm text-muted-foreground space-y-1.5 mb-4 list-disc pl-5" {...props}>
      {children}
    </ul>
  ),
  ol: ({ children, ...props }) => (
    <ol
      className="text-sm text-muted-foreground space-y-1.5 mb-4 list-decimal pl-5"
      {...props}
    >
      {children}
    </ol>
  ),
  li: (props) => (
    <li className="leading-relaxed" {...props} />
  ),
  blockquote: (props) => (
    <blockquote
      className="border-l-2 border-border pl-4 text-muted-foreground text-sm italic my-4"
      {...props}
    />
  ),
  code: ({ className, ...props }) => {
    if (className) {
      return <code className={className} {...props} />;
    }
    return (
      <code
        className="bg-muted px-1.5 py-0.5 border border-border rounded text-xs text-foreground"
        {...props}
      />
    );
  },
  pre: (props) => (
    <pre
      className="bg-muted border border-border rounded-lg p-4 overflow-x-auto text-xs mb-4"
      {...props}
    />
  ),
  strong: (props) => (
    <strong className="text-foreground font-semibold" {...props} />
  ),
  hr: () => <hr className="border-border my-8" />,
  table: (props) => (
    <div className="overflow-x-auto mb-4">
      <table className="w-full text-sm text-muted-foreground border border-border" {...props} />
    </div>
  ),
  th: (props) => (
    <th className="text-left text-foreground font-semibold px-3 py-2 border-b border-border bg-muted" {...props} />
  ),
  td: (props) => (
    <td className="px-3 py-2 border-b border-border" {...props} />
  ),
};
