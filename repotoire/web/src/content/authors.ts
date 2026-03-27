export type Author = {
  id: string;
  name: string;
  role: string;
  avatar?: string;
};

export const authors: Record<string, Author> = {
  zach: {
    id: "zach",
    name: "Zach Hammad",
    role: "Founder",
  },
};
